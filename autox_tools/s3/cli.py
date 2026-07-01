"""CLI entry point for S3/MinIO asset management commands.

Usage::

    uv run s3 list [prefix] [-b BUCKET] [--recursive] [--limit N]
    uv run s3 tree [prefix] [-b BUCKET] [--depth N]
    uv run s3 download <prefix> [-b BUCKET] [--output DIR] [--pattern GLOB]
    uv run s3 upload <local-path> <prefix> [-b BUCKET] [--recursive]
    uv run s3 cleanup <prefix> [-b BUCKET] [--older-than DAYS] [--pattern GLOB] [--dry-run] [--yes]
"""

from __future__ import annotations

import argparse
import fnmatch
import mimetypes
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from autox_tools._output import human_size, print_json
from autox_tools._s3_utils import paginate_objects
from autox_tools.s3._client import connect

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(client: Any, args: argparse.Namespace) -> None:
    """List objects in a bucket, optionally recursively."""
    prefix = args.prefix or ""
    prefixes: list[dict] = []

    if args.recursive:
        result = paginate_objects(client, args.bucket, prefix, max_keys=args.limit)
        objects = result["Contents"]
    else:
        result = paginate_objects(client, args.bucket, prefix, delimiter="/", max_keys=args.limit)
        objects = result["Contents"]
        prefixes = result["CommonPrefixes"]

    if args.json:
        entries = []
        if not args.recursive:
            for p in prefixes:
                entries.append({"key": p["Prefix"], "type": "directory"})
        for obj in objects:
            modified = obj["LastModified"]
            entries.append({
                "key": obj["Key"],
                "type": "file",
                "size_bytes": obj["Size"],
                "last_modified": modified.isoformat() if isinstance(modified, datetime) else str(modified),
            })
        print_json(entries)
        return

    if not args.recursive and prefixes:
        for p in prefixes:
            rel = p["Prefix"]
            if prefix:
                rel = rel[len(prefix):]
            print(f"  {rel}")

    if not objects:
        if not prefixes:
            print("No objects found.")
        return

    max_key = max(len(o["Key"][len(prefix):] if prefix else o["Key"]) for o in objects) if objects else 10
    max_key = max(max_key, 4)

    for obj in objects:
        rel_key = obj["Key"][len(prefix):] if prefix else obj["Key"]
        size = human_size(obj["Size"])
        modified = obj["LastModified"]
        if isinstance(modified, datetime):
            modified = modified.strftime("%Y-%m-%d %H:%M")
        print(f"  {rel_key:<{max_key}}  {size:>10}  {modified}")

    total_size = sum(o["Size"] for o in objects)
    print(f"\n  {len(objects)} object(s), {human_size(total_size)} total")


def cmd_tree(client: Any, args: argparse.Namespace) -> None:
    """Display a tree view of the object hierarchy."""
    prefix = args.prefix or ""
    result = paginate_objects(client, args.bucket, prefix)
    objects = result["Contents"]

    if not objects:
        print("No objects found.")
        return

    tree: dict = {}
    for obj in objects:
        key = obj["Key"]
        rel = key[len(prefix):] if prefix else key
        if not rel:
            continue
        parts = rel.split("/")
        node = tree
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        if parts[-1]:
            node[parts[-1]] = obj["Size"]

    root_label = f"{args.bucket}/{prefix}" if prefix else args.bucket
    print(root_label)
    _render_tree(tree, "", args.depth, 0)


def _render_tree(node: dict, indent: str, max_depth: int, current_depth: int) -> None:
    """Recursively render a tree structure with box-drawing characters."""
    entries = sorted(node.keys(), key=lambda k: (not k.endswith("/"), k))
    for i, key in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        child = node[key]

        if isinstance(child, dict):
            if current_depth >= max_depth:
                count = _count_descendants(child)
                print(f"{indent}{connector}{key} ... ({count} more)")
            else:
                print(f"{indent}{connector}{key}")
                extension = "    " if is_last else "│   "
                _render_tree(child, indent + extension, max_depth, current_depth + 1)
        else:
            print(f"{indent}{connector}{key} ({human_size(child)})")


def _count_descendants(node: dict) -> int:
    """Count total leaf nodes under a tree node."""
    count = 0
    for v in node.values():
        if isinstance(v, dict):
            count += _count_descendants(v)
        else:
            count += 1
    return count


def cmd_download(client: Any, args: argparse.Namespace) -> None:
    """Download objects from S3 to a local directory."""
    result = paginate_objects(client, args.bucket, args.prefix)
    objects = result["Contents"]

    if args.pattern:
        objects = [o for o in objects if fnmatch.fnmatch(os.path.basename(o["Key"]), args.pattern)]

    objects = [o for o in objects if not o["Key"].endswith("/")]

    if not objects:
        print("No matching objects to download.")
        return

    output_dir = args.output
    total_bytes = 0
    for obj in objects:
        key = obj["Key"]
        rel_path = key[len(args.prefix):].lstrip("/")
        local_path = os.path.join(output_dir, rel_path)

        os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
        client.download_file(args.bucket, key, local_path)

        size = obj["Size"]
        total_bytes += size
        print(f"  Downloaded: {rel_path} ({human_size(size)})")

    print(f"\n  Downloaded {len(objects)} file(s), {human_size(total_bytes)} total to {output_dir}/")


def cmd_cleanup(client: Any, args: argparse.Namespace) -> None:
    """Delete old or matching artifacts from S3."""
    result = paginate_objects(client, args.bucket, args.prefix)
    objects = result["Contents"]

    if args.older_than is not None:
        cutoff = datetime.now(UTC) - timedelta(days=args.older_than)
        objects = [o for o in objects if o["LastModified"] < cutoff]

    if args.pattern:
        objects = [o for o in objects if fnmatch.fnmatch(os.path.basename(o["Key"]), args.pattern)]

    if not objects:
        print("No objects match the criteria.")
        return

    total_size = sum(o["Size"] for o in objects)
    print(f"  {len(objects)} object(s) matching criteria ({human_size(total_size)} total)")

    if args.dry_run:
        print("\n  Dry run -- the following would be deleted:")
        for obj in objects:
            print(f"    {obj['Key']} ({human_size(obj['Size'])})")
        return

    if not args.yes:
        answer = input("\n  Confirm deletion? [y/N] ").strip().lower()
        if answer != "y":
            print("  Aborted.")
            return

    batch_size = 1000
    deleted = 0
    keys = [{"Key": o["Key"]} for o in objects]

    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        client.delete_objects(Bucket=args.bucket, Delete={"Objects": batch})
        deleted += len(batch)
        print(f"  Deleted batch: {len(batch)} object(s) ({deleted}/{len(keys)} total)")

    print(f"\n  Deleted {deleted} object(s), {human_size(total_size)} freed.")


def cmd_upload(client: Any, args: argparse.Namespace) -> None:
    """Upload local files to S3."""
    local_path = args.local_path
    is_dir = os.path.isdir(local_path)

    if is_dir and not args.recursive:
        sys.exit("Source is a directory -- use --recursive / -r to upload recursively.")

    if not os.path.exists(local_path):
        sys.exit(f"Path does not exist: {local_path}")

    _ensure_bucket(client, args.bucket)

    if is_dir:
        count, total_bytes = _upload_directory(client, local_path, args.bucket, args.prefix)
    else:
        total_bytes = _upload_file(client, local_path, args.bucket, args.prefix)
        count = 1

    print(f"\n  Uploaded {count} file(s), {human_size(total_bytes)} total to s3://{args.bucket}/{args.prefix}")


def _ensure_bucket(client: Any, bucket: str) -> None:
    """Create bucket if it does not exist."""
    try:
        client.head_bucket(Bucket=bucket)
    except client.exceptions.ClientError:
        endpoint = client.meta.endpoint_url or ""
        if "amazonaws.com" in endpoint:
            region = client.meta.region_name or "us-east-1"
            if region != "us-east-1":
                client.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},
                )
            else:
                client.create_bucket(Bucket=bucket)
        else:
            client.create_bucket(Bucket=bucket)
        print(f"  Created bucket: {bucket}")


def _upload_file(client: Any, local_path: str, bucket: str, prefix: str) -> int:
    """Upload a single file and return its size in bytes."""
    filename = os.path.basename(local_path)
    key = f"{prefix.rstrip('/')}/{filename}" if prefix else filename
    content_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"

    client.upload_file(local_path, bucket, key, ExtraArgs={"ContentType": content_type})
    size = os.path.getsize(local_path)
    print(f"  Uploaded: {key} ({human_size(size)})")
    return size


def _upload_directory(client: Any, directory: str, bucket: str, prefix: str) -> tuple[int, int]:
    """Upload a directory tree and return (file_count, total_bytes)."""
    count = 0
    total_bytes = 0
    base = os.path.normpath(directory)

    for root, _, files in os.walk(base):
        for filename in sorted(files):
            filepath = os.path.join(root, filename)
            rel = os.path.relpath(filepath, base)
            key = f"{prefix.rstrip('/')}/{rel}" if prefix else rel
            content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"

            client.upload_file(filepath, bucket, key, ExtraArgs={"ContentType": content_type})
            size = os.path.getsize(filepath)
            total_bytes += size
            count += 1
            print(f"  Uploaded: {key} ({human_size(size)})")

    return count, total_bytes


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="s3",
        description="Manage S3/MinIO object storage for experiment artifacts.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON (not supported by tree)")

    sub = parser.add_subparsers(dest="command", required=True)

    bucket_help = "Bucket name (default: from .autox.yaml config)"

    # list
    p = sub.add_parser("list", help="List objects in a bucket")
    p.add_argument("prefix", nargs="?", default="", help="Key prefix to filter")
    p.add_argument("--bucket", "-b", default=None, help=bucket_help)
    p.add_argument("--recursive", "-r", action="store_true", help="List all objects recursively")
    p.add_argument("--limit", type=int, default=1000, help="Max objects to return (default: 1000)")

    # tree
    p = sub.add_parser("tree", help="Tree view of object hierarchy")
    p.add_argument("prefix", nargs="?", default="", help="Root prefix")
    p.add_argument("--bucket", "-b", default=None, help=bucket_help)
    p.add_argument("--depth", type=int, default=3, help="Max directory depth (default: 3)")

    # download
    p = sub.add_parser("download", help="Download objects to local directory")
    p.add_argument("prefix", help="S3 key prefix to download from")
    p.add_argument("--bucket", "-b", default=None, help=bucket_help)
    p.add_argument("--output", "-o", default=".", help="Local destination directory (default: .)")
    p.add_argument("--pattern", help="Glob pattern to filter keys (e.g. '*.json')")

    # upload
    p = sub.add_parser("upload", help="Upload local files to S3")
    p.add_argument("local_path", help="Local file or directory")
    p.add_argument("prefix", help="S3 key prefix")
    p.add_argument("--bucket", "-b", default=None, help=bucket_help)
    p.add_argument("--recursive", "-r", action="store_true", help="Upload entire directory tree")

    # cleanup
    p = sub.add_parser("cleanup", help="Delete old or matching artifacts")
    p.add_argument("prefix", help="S3 key prefix scope")
    p.add_argument("--bucket", "-b", default=None, help=bucket_help)
    p.add_argument("--older-than", type=int, default=None, help="Only delete objects older than N days")
    p.add_argument("--pattern", help="Glob pattern to filter keys")
    p.add_argument("--dry-run", action="store_true", help="List what would be deleted without acting")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    return parser


def _resolve_bucket(args: argparse.Namespace, cfg: Any) -> str:
    """Resolve bucket from CLI ``--bucket`` flag, falling back to config."""
    bucket = args.bucket or ""
    if bucket:
        return bucket
    if cfg is not None and getattr(cfg, "bucket", ""):
        return cfg.bucket
    sys.exit(
        "No bucket specified. Provide --bucket / -b on the command line "
        "or set 'bucket' in your S3 config in .autox.yaml."
    )


def main() -> None:
    parser = _build_parser()

    from autox_tools.config._loader import add_profile_args, resolve
    add_profile_args(parser, target=True)

    args = parser.parse_args()
    cfg = resolve("s3", args)
    args.bucket = _resolve_bucket(args, cfg)
    client = connect(cfg)

    commands: dict[str, Any] = {
        "list": cmd_list,
        "tree": cmd_tree,
        "download": cmd_download,
        "upload": cmd_upload,
        "cleanup": cmd_cleanup,
    }
    commands[args.command](client, args)


if __name__ == "__main__":
    main()
