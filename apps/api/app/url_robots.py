"""Bounded robots matching: agent groups, longest rules, wildcards and delays."""
from dataclasses import dataclass
import math
import re
from urllib.parse import quote

from .url_guard import FetchError

UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def normalized_path(value):
    value = quote(value, safe="/%?:@!$&'()*+,;=-._~")
    def escape(match):
        char = chr(int(match[0][1:], 16))
        return char if char in UNRESERVED else match[0].upper()
    return re.sub(r"%[0-9A-Fa-f]{2}", escape, value)


def matches(pattern, path):
    anchored = pattern.endswith("$")
    if anchored: pattern = pattern[:-1]
    parts = pattern.split("*")
    if not path.startswith(parts[0]): return False
    position = len(parts[0])
    if len(parts) == 1: return not anchored or position == len(path)
    for index, part in enumerate(parts[1:], 1):
        if anchored and index == len(parts) - 1:
            return path.endswith(part) and len(path) - len(part) >= position
        found = path.find(part, position)
        if found < 0: return False
        position = found + len(part)
    return True


@dataclass(frozen=True)
class RobotsPolicy:
    rules: tuple = ()
    interval: float = 1

    def allows(self, path):
        path = normalized_path(path)
        matching = [(len(pattern.replace("*", "").rstrip("$")), allowed)
                    for pattern, allowed in self.rules if matches(pattern, path)]
        return max(matching, default=(0, True))[1]  # Allow wins equal-specificity ties.


def parse_robots(text):
    lines = text.splitlines()
    if len(lines) > 2000 or any(len(line) > 4096 for line in lines):
        raise FetchError("robots_too_complex")
    groups, group, has_fields = [], None, False
    try:
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line: continue
            name, value = (part.strip() for part in line.split(":", 1))
            name = name.lower()
            if name == "user-agent":
                if group is None or has_fields:
                    group = {"agents": [], "rules": [], "interval": 1}
                    groups.append(group)
                    has_fields = False
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}|\*", value): raise ValueError()
                group["agents"].append(value.lower())
            elif group is not None:
                if name in {"allow", "disallow", "crawl-delay", "request-rate"}: has_fields = True
                if name in {"allow", "disallow"} and value:
                    if len(value) > 2048 or not value.startswith(("/", "*")): raise ValueError()
                    group["rules"].append((normalized_path(value), name == "allow"))
                elif name == "crawl-delay":
                    delay = float(value)
                    if not math.isfinite(delay) or not 0 <= delay <= 86400: raise ValueError()
                    group["interval"] = max(group["interval"], delay)
                elif name == "request-rate":
                    if not re.fullmatch(r"[1-9][0-9]{0,5}/[1-9][0-9]{0,5}", value): raise ValueError()
                    count, seconds = map(int, value.split("/"))
                    group["interval"] = max(group["interval"], seconds / count)
        product = "termslens"
        ranks = [(max((len(agent) for agent in item["agents"] if agent != "*" and agent in product), default=0), item)
                 for item in groups]
        best = max((rank for rank, _ in ranks), default=0)
        selected = [item for rank, item in ranks if (rank == best if best else "*" in item["agents"])]
        return RobotsPolicy(tuple(rule for item in selected for rule in item["rules"]),
                            max((item["interval"] for item in selected), default=1))
    except ValueError:
        raise FetchError("invalid_robots") from None
