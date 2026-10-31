"""Observables: typed indicators, extraction from free text, and (de)fanging."""

import ipaddress
import re
from collections.abc import Iterator
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ObservableType(StrEnum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    DOMAIN = "domain"
    URL = "url"
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    EMAIL = "email"
    FILENAME = "filename"
    REGISTRY_KEY = "registry_key"
    USER = "user"
    PROCESS = "process"
    CVE = "cve"


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ObservableType
    value: str


_REFANG = [
    (re.compile(r"\bhxxp", re.IGNORECASE), "http"),
    (re.compile(r"\bfxp(?=s?\[?:)", re.IGNORECASE), "ftp"),
    (re.compile(r"\[://\]"), "://"),
    (re.compile(r"\[:\]"), ":"),
    (re.compile(r"\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)|\{dot\}", re.IGNORECASE), "."),
    (re.compile(r"\[@\]|\(@\)|\{@\}|\[at\]|\(at\)", re.IGNORECASE), "@"),
]


def refang(text: str) -> str:
    for pattern, replacement in _REFANG:
        text = pattern.sub(replacement, text)
    return text


def defang(type_: ObservableType, value: str) -> str:
    """A form safe to display or paste: not clickable, not resolvable."""
    if type_ in (ObservableType.URL, ObservableType.DOMAIN, ObservableType.IPV4):
        value = value.replace(".", "[.]")
    if type_ is ObservableType.URL:
        value = re.sub(r"^http", "hxxp", value, flags=re.IGNORECASE)
        value = re.sub(r"^ftp", "fxp", value, flags=re.IGNORECASE)
    if type_ is ObservableType.EMAIL:
        local, _, domain = value.partition("@")
        value = f"{local}[@]{domain.replace('.', '[.]')}"
    return value


_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_TLD = r"[A-Za-z][A-Za-z0-9-]{0,61}[A-Za-z0-9]"
_DOMAIN = rf"(?:{_LABEL}\.)+{_TLD}"
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
# Extensions that make a dotted word a file name rather than a domain, even where the
# extension is also a top-level domain (.zip, .md, .py, ...).
_FILE_EXTENSIONS = (
    "exe|dll|sys|scr|ps1|psm1|bat|cmd|vbs|vbe|js|jse|wsf|hta|jar|lnk|msi|iso|img|"
    "zip|rar|7z|gz|tar|pdf|docx?|docm|xlsx?|xlsm|pptx?|rtf|txt|csv|html?|png|jpe?g|gif|"
    "md|py|sh|pl|rb|go|rs|json|ya?ml|xml|log|ini|conf|cfg"
)

_FULL = {
    ObservableType.DOMAIN: re.compile(_DOMAIN),
    ObservableType.URL: re.compile(r"(?:https?|ftp)://\S+", re.IGNORECASE),
    ObservableType.MD5: re.compile(r"[0-9a-f]{32}"),
    ObservableType.SHA1: re.compile(r"[0-9a-f]{40}"),
    ObservableType.SHA256: re.compile(r"[0-9a-f]{64}"),
    ObservableType.EMAIL: re.compile(rf"[A-Za-z0-9._%+-]+@{_DOMAIN}"),
    ObservableType.CVE: re.compile(r"CVE-\d{4}-\d{4,}"),
}
_HASHES = {32: ObservableType.MD5, 40: ObservableType.SHA1, 64: ObservableType.SHA256}

# Matched in this order; a later pattern never matches inside an earlier match.
_PATTERNS: list[tuple[ObservableType | None, re.Pattern[str]]] = [
    (ObservableType.URL, re.compile(r"\b(?:https?|ftp)://[^\s<>\"'`]+", re.IGNORECASE)),
    (ObservableType.EMAIL, re.compile(rf"\b[A-Za-z0-9._%+-]+@{_DOMAIN}\b")),
    (
        ObservableType.REGISTRY_KEY,
        re.compile(r"\b(?:HKEY_[A-Z_]+|HKLM|HKCU|HKCR|HKU|HKCC)\\[^\s\"'<>]+"),
    ),
    (ObservableType.CVE, re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)),
    (None, re.compile(r"\b[0-9a-fA-F]{32}(?:[0-9a-fA-F]{8})?(?:[0-9a-fA-F]{24})?\b")),
    (ObservableType.IPV4, re.compile(rf"(?<![\d.])(?:{_OCTET}\.){{3}}{_OCTET}(?!\d|\.\d)")),
    (
        ObservableType.IPV6,
        re.compile(r"(?<![\w:])[0-9A-Fa-f:]*:[0-9A-Fa-f:]*:[0-9A-Fa-f:]*(?![\w:])"),
    ),
    (
        ObservableType.FILENAME,
        re.compile(rf"(?<![\w.-])[\w-][\w.-]*\.(?:{_FILE_EXTENSIONS})\b", re.IGNORECASE),
    ),
    (ObservableType.DOMAIN, re.compile(rf"(?<![\w.@-]){_DOMAIN}(?![\w-]|\.[A-Za-z0-9])")),
]

_TRAILING = ".,;:!?)]}'\""


def normalize(type_: ObservableType, value: str) -> str:
    """The canonical form of a value; raises ValueError if it is not that type."""
    value = refang(value.strip())
    match type_:
        case ObservableType.IPV4 | ObservableType.IPV6:
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                raise ValueError(f"not a valid {type_}") from None
            if address.version != (4 if type_ is ObservableType.IPV4 else 6):
                raise ValueError(f"not a valid {type_}")
            return address.compressed
        case ObservableType.DOMAIN | ObservableType.EMAIL:
            value = value.lower().rstrip(".")
        case ObservableType.MD5 | ObservableType.SHA1 | ObservableType.SHA256:
            value = value.lower()
        case ObservableType.CVE:
            value = value.upper()
        case _:
            pass
    pattern = _FULL.get(type_)
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(f"not a valid {type_}")
    if not value or len(value) > 2000 or "\n" in value:
        raise ValueError(f"not a valid {type_}")
    return value


def _classify(type_: ObservableType | None, raw: str) -> tuple[ObservableType, str] | None:
    if type_ is None:
        type_ = _HASHES.get(len(raw))
        if type_ is None:
            return None
    if type_ is ObservableType.URL:
        raw = raw.rstrip(_TRAILING)
    if type_ is ObservableType.IPV6 and not any(c.isalnum() for c in raw):
        return None  # a bare "::" is punctuation, not an address
    try:
        return type_, normalize(type_, raw)
    except ValueError:
        return None


def extract(text: str) -> list[Candidate]:
    """Find observables in free text (email, logs, notes), de-duplicated in order of appearance."""
    text = refang(text)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, Candidate]] = []
    for type_, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            classified = _classify(type_, match.group())
            if classified is None:
                continue
            taken.append((start, end))
            found.append((start, Candidate(type=classified[0], value=classified[1])))
    unique = dict.fromkeys(c for _, c in sorted(found, key=lambda item: item[0]))
    return list(unique)


def strings_in(payload: Any) -> Iterator[str]:
    """Every string in a JSON value, so extraction sees text rather than JSON escapes."""
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, dict):
        for key, value in payload.items():
            yield str(key)
            yield from strings_in(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from strings_in(item)
