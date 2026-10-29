"""Static text/headings and same-origin candidate extraction; no browser execution."""
from email.message import Message
from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from .url_guard import FetchError, validate_url

EXTRACTOR = "static-policy-v1"
HIDDEN = {"head", "script", "style", "template", "nav", "footer", "form", "button", "svg", "canvas"}
BLOCKS = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote", "dt", "dd"}


def decode_body(body, content_type):
    if not content_type: raise FetchError("unsupported_content_type")
    header = Message()
    header["content-type"] = content_type or ""
    media = header.get_content_type().lower()
    if media not in {"text/html", "text/plain"}: raise FetchError("unsupported_content_type")
    charset = header.get_content_charset()
    if not charset and media == "text/html":
        declared = re.search(br'<meta\b[^>]{0,300}\bcharset\s*=\s*["\x27]?([A-Za-z0-9_-]+)', body[:8192], re.I)
        if declared: charset = declared[1].decode("ascii")
    try:
        return body.decode(charset or "utf-8-sig"), media
    except (UnicodeError, LookupError):
        raise FetchError("unsupported_text_encoding") from None


class StaticPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden, self.heading, self.in_title = 0, None, False
        self.buffer, self.lines, self.links, self.title = [], [], [], []
        self.anchor, self.anchor_text, self.language = None, [], None
        self.links_limited = False

    def flush(self):
        text = " ".join("".join(self.buffer).split())
        if text: self.lines.append((text, self.heading))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html": self.language = attrs.get("lang")
        if tag == "title": self.in_title = True
        if tag in HIDDEN:
            if not self.hidden: self.flush()
            self.hidden += 1
        # Links in navigation/footer are useful for discovery, but never become policy text.
        if tag == "a" and attrs.get("href"):
            self.anchor, self.anchor_text = attrs["href"], []
        if self.hidden: return
        if tag in BLOCKS: self.flush()
        if re.fullmatch(r"h[1-6]", tag): self.heading = int(tag[1])

    def handle_endtag(self, tag):
        if tag == "title": self.in_title = False
        if tag == "a" and self.anchor is not None:
            if len(self.links) < 256: self.links.append((self.anchor, " ".join("".join(self.anchor_text).split())[:160]))
            else: self.links_limited = True
            self.anchor, self.anchor_text = None, []
        if tag in HIDDEN:
            self.hidden = max(0, self.hidden - 1)
            return
        if self.hidden: return
        if tag in BLOCKS: self.flush()
        if re.fullmatch(r"h[1-6]", tag): self.heading = None

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.in_title: self.title.append(data)
        if self.anchor is not None: self.anchor_text.append(data)
        if not self.hidden: self.buffer.append(data)

    def document(self):
        self.flush()
        parts, headings, offset = [], [], 0
        for text, level in self.lines:
            if parts: offset += 1
            if level is not None: headings.append({"text": text, "level": level, "start": offset, "end": offset + len(text)})
            parts.append(text)
            offset += len(text)
        return "\n".join(parts), headings


def extract(body, content_type, *, discovery=False):
    decoded, media = decode_body(body, content_type)
    page = StaticPage()
    if media == "text/html":
        page.feed(decoded)
        page.close()
        text, headings = page.document()
    else:
        text, headings = decoded, []
    title = " ".join("".join(page.title).split())[:160]
    if title.lower().strip(" .!") in {"just a moment", "access denied", "attention required", "verify you are human"}:
        raise FetchError("blocked_or_dynamic_page")
    if page.language and not page.language.lower().split("-")[0] == "en":
        raise FetchError("non_english_page")
    if not text.strip() and not discovery: raise FetchError("empty_static_text")
    if len(text) > 200000 or len(text.split()) > 25000 or len(headings) > 2000:
        raise FetchError("document_too_large")
    return {"text": text, "headings": headings, "title": title, "extractor": EXTRACTOR}, page


def candidates(page, target):
    found = []
    for href, label in page.links:
        if href.startswith("#"): continue
        try:
            linked = validate_url(urljoin(target.url, href))
        except FetchError:
            continue
        if linked.origin != target.origin or linked.url == target.url or any(item["url"] == linked.url for item in found): continue
        words = (label + " " + linked.path).lower()
        document_type = next((kind for kind, pattern in (
            ("privacy", r"\bprivacy\b"), ("cookies", r"\bcookies?\b"),
            ("refund", r"\b(refunds?|returns?|cancellation)\b"), ("terms", r"\b(terms|conditions|tos)\b")) if re.search(pattern, words)), None)
        if document_type:
            found.append({"url": linked.url, "title": label or document_type.title(), "document_type": document_type})
            if len(found) == 4: break
    return found
