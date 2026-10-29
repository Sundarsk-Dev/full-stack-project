"""Public URL validation and DNS-pinned HTTP. Use only inside url_runtime's deadline."""
from dataclasses import dataclass
import http.client
import ipaddress
import re
import socket
import ssl
from urllib.parse import quote, urlsplit, urlunsplit
import zlib

MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "TermsLens/0.1"
ERROR_CODES = frozenset({"nonpublic_address", "invalid_url", "invalid_dns_answers", "dns_failed", "peer_mismatch",
    "unsupported_encoding", "response_too_large", "invalid_compression", "incomplete_response", "network_failed",
    "robots_too_complex", "invalid_robots", "unsupported_content_type", "unsupported_text_encoding",
    "blocked_or_dynamic_page", "non_english_page", "empty_static_text", "document_too_large", "invalid_host_budget",
    "host_busy", "host_throttled", "invalid_deadline", "fetch_deadline", "request_limit", "redirect_limit",
    "redirect_missing_location", "cross_origin_redirect", "robots_denied", "robots_unavailable", "invalid_operation",
    "http_unavailable", "storage_unavailable", "invalid_child_request", "invalid_child_result", "fetch_failed"})


class FetchError(Exception):
    """Only fixed codes; URLs, response bodies and raw exceptions remain private."""

    def __init__(self, code):
        super().__init__(code if code in ERROR_CODES else "fetch_failed")


def public_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if (not address.is_global or address.is_multicast or address.is_reserved
                or address.is_loopback or address.is_link_local or address.is_unspecified):
            raise ValueError()
        if isinstance(address, ipaddress.IPv6Address) and (
                address.ipv4_mapped or address.sixtofour or address.teredo
                or address in ipaddress.ip_network("64:ff9b::/96")
                or address in ipaddress.ip_network("64:ff9b:1::/48")):
            raise ValueError()
        return address
    except ValueError:
        raise FetchError("nonpublic_address") from None


@dataclass(frozen=True)
class Target:
    url: str
    scheme: str
    host: str
    port: int
    authority: str
    path: str

    @property
    def origin(self):
        return self.scheme, self.host, self.port


def validate_url(value):
    try:
        if (not isinstance(value, str) or not 1 <= len(value) <= 2048 or "\\" in value
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
            raise ValueError()
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if "%" in host:
            raise ValueError()
        port = parsed.port if parsed.port is not None else 443 if parsed.scheme == "https" else 80
        if port not in {80, 443}:
            raise ValueError()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
            if (len(host) > 253 or "." not in host or all(c.isdigit() or c == "." for c in host)
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split("."))):
                raise ValueError()
        if literal is not None:
            host = str(public_ip(host))
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parsed.scheme == "https" else 80): authority += f":{port}"
        path = parsed.path or "/"
        if re.search(r"%(?![0-9a-fA-F]{2})", path + parsed.query): raise ValueError()
        path = quote(path, safe="/%:@!$&'()*+,;=-._~")
        query = quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        url = urlunsplit((parsed.scheme, authority, path, query, ""))
        if len(url) > 2048: raise ValueError()
        return Target(url, parsed.scheme, host, port, authority, path + ("?" + query if query else ""))
    except (ValueError, UnicodeError):
        raise FetchError("invalid_url") from None


@dataclass(frozen=True)
class Address:
    family: int
    ip: str


def resolve_public(target, resolver=None):
    try:
        answers = (resolver or socket.getaddrinfo)(target.host, target.port, type=socket.SOCK_STREAM)
        if not 1 <= len(answers) <= 64: raise FetchError("invalid_dns_answers")
        addresses = []
        for family, _kind, _protocol, _name, sockaddr in answers:
            address = public_ip(sockaddr[0])
            expected = socket.AF_INET if address.version == 4 else socket.AF_INET6
            if family != expected: raise FetchError("invalid_dns_answers")
            candidate = Address(family, str(address))
            if candidate not in addresses: addresses.append(candidate)
        return addresses
    except FetchError:
        raise
    except Exception:
        raise FetchError("dns_failed") from None


class PinnedConnection(http.client.HTTPConnection):
    def __init__(self, target, address, timeout):
        super().__init__(target.host, target.port, timeout=timeout)
        self.target, self.address = target, address

    def connect(self):
        # Numeric sockaddr bypasses any second DNS lookup, including during TLS setup.
        public_ip(self.address.ip)
        sock = socket.socket(self.address.family, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            endpoint = (self.address.ip, self.target.port)
            sock.connect(endpoint if self.address.family == socket.AF_INET else (*endpoint, 0, 0))
            if str(public_ip(sock.getpeername()[0])) != self.address.ip:
                raise FetchError("peer_mismatch")
            if self.target.scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=self.target.host)
            self.sock = sock
        except BaseException:
            sock.close()
            raise


@dataclass(frozen=True)
class HTTPResult:
    status: int
    headers: dict
    body: bytes  # Decompressed, bounded source bytes.


def read_body(response):
    encoding = response.getheader("Content-Encoding", "identity").strip().lower()
    if encoding not in {"identity", "gzip"}: raise FetchError("unsupported_encoding")
    length = response.getheader("Content-Length")
    if length is not None:
        try:
            if not 0 <= int(length) <= MAX_BYTES: raise ValueError()
        except ValueError:
            raise FetchError("response_too_large") from None
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
    chunks, wire_size, size = [], 0, 0
    while chunk := response.read(65536):
        wire_size += len(chunk)
        if wire_size > MAX_BYTES: raise FetchError("response_too_large")
        try:
            data = decoder.decompress(chunk, MAX_BYTES - size + 1) if decoder else chunk
        except zlib.error:
            raise FetchError("invalid_compression") from None
        size += len(data)
        if size > MAX_BYTES: raise FetchError("response_too_large")
        chunks.append(data)
        if decoder and (decoder.unconsumed_tail or decoder.unused_data):
            raise FetchError("invalid_compression")
    if decoder and not decoder.eof: raise FetchError("invalid_compression")
    if length is not None and wire_size != int(length): raise FetchError("incomplete_response")
    return b"".join(chunks)


def request_pinned(target, address, timeout):
    connection = PinnedConnection(target, address, min(timeout, 10))
    try:
        connection.request("GET", target.path, headers={"Host": target.authority, "User-Agent": USER_AGENT,
            "Accept": "text/html,text/plain", "Accept-Encoding": "gzip", "Connection": "close"})
        response = connection.getresponse()
        headers = {name: response.getheader(name) for name in ("Content-Type", "Location")}
        return HTTPResult(response.status, headers, read_body(response))
    except FetchError:
        raise
    except Exception:
        raise FetchError("network_failed") from None
    finally:
        connection.close()
