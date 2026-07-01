"""Shared S3 utilities used across CLI modules."""

from __future__ import annotations

import os
from typing import Any

from autox_tools._output import human_size


def paginate_objects(
    client: Any,
    bucket: str,
    prefix: str,
    *,
    delimiter: str = "",
    max_keys: int = 0,
) -> dict:
    """Paginate through ``list_objects_v2`` and return aggregated results.

    Returns a dict with ``Contents`` and ``CommonPrefixes`` lists.
    """
    contents: list[dict] = []
    common_prefixes: list[dict] = []
    kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
    if delimiter:
        kwargs["Delimiter"] = delimiter

    while True:
        resp = client.list_objects_v2(**kwargs)
        contents.extend(resp.get("Contents", []))
        common_prefixes.extend(resp.get("CommonPrefixes", []))

        if max_keys and len(contents) >= max_keys:
            contents = contents[:max_keys]
            break

        if not resp.get("IsTruncated"):
            break
        kwargs["ContinuationToken"] = resp["NextContinuationToken"]

    return {"Contents": contents, "CommonPrefixes": common_prefixes}


def download_objects(
    s3_client: Any,
    bucket: str,
    objects: list[dict[str, Any]],
    base_prefix: str,
    download_dir: str,
) -> None:
    """Download S3 objects to a local directory, preserving relative paths."""
    os.makedirs(download_dir, exist_ok=True)
    downloaded = 0
    total_bytes = 0

    for obj in objects:
        key = obj["Key"]
        if key.endswith("/"):
            continue
        rel = key[len(base_prefix):].lstrip("/") if base_prefix else key
        local_path = os.path.join(download_dir, rel)
        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        s3_client.download_file(bucket, key, local_path)
        downloaded += 1
        total_bytes += obj.get("Size", 0)
        print(f"  Downloaded: {rel} ({human_size(obj.get('Size', 0))})")

    print(f"\n  {downloaded} file(s), {human_size(total_bytes)} total to {download_dir}/")
