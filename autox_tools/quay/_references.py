"""Validate image references before sending authenticated requests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_TAG = re.compile(r"[\w][\w.-]{0,127}", re.ASCII)
_DIGEST = re.compile(r"sha256:[0-9a-fA-F]{64}")


def registry_host(base_url: str) -> str:
    """Require a registry origin, without credentials, paths or query strings."""
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Quay base_url must be an http(s) registry origin, such as https://quay.io")
    # Accessing port also validates its syntax and range.
    _ = parsed.port
    return parsed.netloc.lower()


@dataclass(frozen=True)
class Reference:
    repository: str
    tag: str | None = None
    digest: str | None = None


def parse_reference(value: str, host: str, namespace: str = "", *, image: bool = False) -> Reference:
    """Parse nested repositories and explicit tags/digests; never infer a tag."""
    if "://" in value or value != value.strip():
        raise ValueError("Use a pull reference without a URL scheme or whitespace")
    digest = tag = None
    if "@" in value:
        value, digest = value.rsplit("@", 1)
        if not _DIGEST.fullmatch(digest):
            raise ValueError("Expected a sha256 digest with 64 hexadecimal characters")
        digest = digest.lower()
    elif ":" in value.rsplit("/", 1)[-1]:
        value, tag = value.rsplit(":", 1)
        if not _TAG.fullmatch(tag):
            raise ValueError("Invalid image tag")

    first, sep, rest = value.partition("/")
    if sep and (first.lower() == host or "." in first or ":" in first or first == "localhost"):
        if first.lower() != host:
            raise ValueError(f"Reference host differs from {host}; select the appropriate Quay --target")
        value = rest
        if "/" not in value:
            raise ValueError("Qualified references must include a namespace and repository")
    elif not sep:
        if not namespace:
            raise ValueError("Bare repository names require QUAY_NAMESPACE or a configured namespace")
        value = f"{namespace}/{value}"

    if any(not _COMPONENT.fullmatch(part) for part in value.split("/")) or "/" not in value:
        raise ValueError("Invalid repository path")
    if image and not (tag or digest):
        raise ValueError("latest requires an exact repository:tag or repository@sha256:digest")
    if not image and (tag or digest):
        raise ValueError("Expected a repository without a tag or digest")
    return Reference(value, tag, digest)
