"""Quay HTTP API access, pagination, and normalized image records."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx
from dotenv import find_dotenv, load_dotenv

from autox_tools.config._models import QuayConfig
from autox_tools.quay._references import registry_host

if TYPE_CHECKING:
    from autox_tools.quay._filters import TagFilters
    from autox_tools.quay._references import Reference


class QuayError(Exception):
    """An actionable configuration or API failure."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ImageNotFoundError(QuayError):
    """No active image satisfies the request."""


def resolve_settings(cfg: QuayConfig | None = None) -> QuayConfig:
    if cfg is None:
        load_dotenv(find_dotenv(usecwd=True))
        cfg = QuayConfig(
            base_url=os.getenv("QUAY_BASE_URL", "https://quay.io"),
            namespace=os.getenv("QUAY_NAMESPACE", ""),
            token=os.getenv("QUAY_TOKEN", ""),
        )
    if not all(isinstance(value, str) for value in (cfg.base_url, cfg.namespace, cfg.token)):
        raise QuayError("Quay base_url, namespace and token must be strings")
    try:
        registry_host(cfg.base_url)
    except ValueError as exc:
        raise QuayError(str(exc)) from exc
    return cfg


def _updated_at(tag: dict) -> str | None:
    try:
        if tag.get("start_ts") is not None:
            return datetime.fromtimestamp(float(tag["start_ts"]), UTC).isoformat()
        if tag.get("last_modified"):
            updated = parsedate_to_datetime(tag["last_modified"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            return updated.astimezone(UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


def _ended_at(tag: dict) -> str | None:
    try:
        if tag.get("end_ts") is not None:
            return datetime.fromtimestamp(float(tag["end_ts"]), UTC).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    return None


class QuayClient:
    def __init__(self, settings: QuayConfig, *, transport: httpx.BaseTransport | None = None):
        self.host = registry_host(settings.base_url)
        self.http = httpx.Client(
            base_url=settings.base_url.rstrip("/") + "/api/v1/",
            headers={"Authorization": f"Bearer {settings.token}"} if settings.token else {},
            timeout=30,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = self.http.get(path, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt < 2:
                    time.sleep(0.5 * 2**attempt)
                    continue
                raise QuayError(f"Cannot reach Quay at {self.host} after 3 attempts ({type(exc).__name__})") from exc
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            if response.status_code in (401, 403):
                raise QuayError("Quay denied access; check your Quay API bearer token and permissions",
                                response.status_code)
            if response.status_code == 404:
                raise QuayError("Quay repository or manifest not found (or not accessible)", 404)
            if not response.is_success:
                raise QuayError(f"Quay request failed with HTTP {response.status_code}", response.status_code)
            try:
                data = response.json()
            except ValueError as exc:
                raise QuayError("Quay returned invalid JSON") from exc
            if not isinstance(data, dict):
                raise QuayError("Quay returned an unexpected response (expected an object)")
            return data
        raise AssertionError("unreachable")

    def record(self, repository: str, tag: dict) -> dict[str, Any]:
        name, digest = tag.get("name"), tag.get("manifest_digest")
        return {
            "repository": repository,
            "tag": name,
            "digest": digest,
            "tag_ref": f"{self.host}/{repository}:{name}" if name else None,
            "digest_ref": f"{self.host}/{repository}@{digest}" if digest else None,
            "updated_at": _updated_at(tag),
            "ended_at": _ended_at(tag),
            "is_active": tag.get("end_ts") is None if name else None,
            "size": tag.get("size"),
            "is_manifest_list": tag.get("is_manifest_list"),
            "media_type": tag.get("media_type"),
            "platforms": None,
        }

    def tags(
        self, repository: str, filters: TagFilters, limit: int | None = 20,
    ) -> dict[str, Any]:
        items: list[dict] = []
        seen: set[str] = set()
        page = 1
        while True:
            params: dict[str, Any] = {"onlyActiveTags": "true", "limit": 100, "page": page}
            if filters.prefix and re.fullmatch(r"[\w.-]+", filters.prefix, re.ASCII):
                # Quay uses SQL LIKE wildcards. An underscore can broaden the
                # server match; the local filter still enforces the literal prefix.
                params["filter_tag_name"] = f"like:{filters.prefix}%"
            data = self.get(f"repository/{quote(repository, safe='/')}/tag/", params)
            rows = data.get("tags", [])
            new_names = 0
            for tag in rows:
                name = tag.get("name")
                if not name or name in seen or tag.get("end_ts") is not None:
                    continue
                seen.add(name)
                new_names += 1
                if not filters.accepts(name):
                    continue
                # Read one extra match so truncated is truthful even if remaining
                # pages contain only excluded tags. Preserve Quay's history order.
                if limit is not None and len(items) == limit:
                    return {"items": items, "returned": len(items), "truncated": True}
                items.append(self.record(repository, tag))
            if not data.get("has_additional"):
                return {"items": items, "returned": len(items), "truncated": False}
            if not new_names and (not rows or all(row.get("name") in seen for row in rows)):
                raise QuayError("Quay tag pagination stopped advancing; retry the request")
            page += 1

    def search(self, query: str, org: str | None, limit: int | None = 20) -> dict[str, Any]:
        items: list[dict] = []
        seen: set[str] = set()
        page = 1
        truncated = False
        while True:
            data = self.get("find/repositories", {"query": f"{org}/{query}" if org else query, "page": page})
            new_names = 0
            for row in data.get("results", []):
                if row.get("kind", "repository") != "repository":
                    continue
                namespace = row.get("namespace") or {}
                namespace = namespace.get("name") if isinstance(namespace, dict) else namespace
                if not namespace or not row.get("name"):
                    continue
                repository = f"{namespace}/{row['name']}"
                if repository in seen:
                    continue
                seen.add(repository)
                new_names += 1
                if org and namespace != org:
                    continue
                if limit is not None and len(items) == limit:
                    truncated = True
                    break
                items.append({"repository": repository, "description": row.get("description"),
                              "is_public": row.get("is_public"), "ref": f"{self.host}/{repository}"})
            if truncated or not data.get("has_additional"):
                break
            if not new_names:
                truncated = True
                break
            page += 1
        return {"items": items, "returned": len(items), "truncated": truncated,
                "search_may_be_capped": True}

    def latest(self, reference: Reference, limit: int = 1) -> dict[str, Any]:
        """Resolve a current tag/digest or the last N retained tag assignments."""
        repository = reference.repository
        try:
            if reference.tag:
                listing = self._tag_history(repository, reference.tag, limit)
                if not listing["items"]:
                    qualifier = "active tag" if limit == 1 else "tag history for"
                    raise ImageNotFoundError(f"No {qualifier} '{reference.tag}' in {repository}")
            else:
                if limit != 1:
                    raise QuayError("A digest identifies one image; -n requires a tagged reference")
                listing = {"items": [self.record(repository, {"manifest_digest": reference.digest})]}
            for record in listing["items"]:
                try:
                    self._manifest(record)
                except QuayError as exc:
                    if exc.status != 404 or record["is_active"] is not False:
                        raise
                    # Retained tag history can outlive the manifest itself.
                    record["manifest_available"] = False
            return dict(listing["items"][0]) if limit == 1 else listing
        except QuayError as exc:
            if exc.status == 404:
                raise ImageNotFoundError(str(exc)) from exc
            raise

    def _tag_history(self, repository: str, name: str, limit: int) -> dict[str, Any]:
        items: list[dict] = []
        seen: set[tuple] = set()
        page = 1
        while True:
            params = {"onlyActiveTags": "true" if limit == 1 else "false",
                      "filter_tag_name": f"eq:{name}", "limit": min(100, limit + 1), "page": page}
            data = self.get(f"repository/{quote(repository, safe='/')}/tag/", params)
            new_rows = 0
            for tag in data.get("tags", []):
                identity = (tag.get("name"), tag.get("manifest_digest"), tag.get("start_ts"), tag.get("end_ts"))
                if identity in seen:
                    continue
                seen.add(identity)
                new_rows += 1
                if tag.get("name") != name or (limit == 1 and tag.get("end_ts") is not None):
                    continue
                if len(items) == limit:
                    return {"items": items, "returned": len(items), "truncated": True}
                items.append(self.record(repository, tag))
                if limit == 1:
                    return {"items": items, "returned": 1, "truncated": False}
            if not data.get("has_additional"):
                return {"items": items, "returned": len(items), "truncated": False}
            if not new_rows:
                raise QuayError("Quay tag history pagination stopped advancing; retry the request")
            page += 1

    def _manifest(self, record: dict[str, Any]) -> dict[str, Any]:
        """Fetch manifest details by digest, preserving the tag assignment metadata."""
        digest = record["digest"]
        if not digest:
            raise QuayError("Quay did not provide a digest for the selected tag")
        repository = quote(record["repository"], safe="/")
        data = self.get(f"repository/{repository}/manifest/{quote(digest, safe=':')}")
        record["manifest_available"] = True
        raw = data.get("manifest_data") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError as exc:
                raise QuayError("Quay returned invalid manifest JSON") from exc
        if not isinstance(raw, dict):
            raise QuayError("Quay returned an invalid manifest object")
        record["media_type"] = raw.get("mediaType")
        record["is_manifest_list"] = data.get("is_manifest_list", record["is_manifest_list"])
        if "manifests" in raw:
            record["platforms"] = [
                {"digest": child.get("digest"), "media_type": child.get("mediaType"),
                 "os": (child.get("platform") or {}).get("os"),
                 "architecture": (child.get("platform") or {}).get("architecture"),
                 "variant": (child.get("platform") or {}).get("variant")}
                for child in raw["manifests"]
            ]
        if record["size"] is None and not record["is_manifest_list"]:
            record["size"] = data.get("layers_compressed_size")
        return dict(record)
