"""CLI entry point for Kubernetes secret management commands.

Usage::

    uv run secrets list [-n NAMESPACE] [--filter PATTERN] [--labels KEY=VAL,...] [--json]
    uv run secrets reveal <name> [-n NAMESPACE] [--json]
    uv run secrets create <name> --from-literal KEY=VALUE [...] [--from-env-file FILE] [-y] [--json]
    uv run secrets edit <name> --set KEY=VALUE [...] --remove KEY [...] [-y] [--json]
    uv run secrets delete <name> [-n NAMESPACE] [-y] [--json]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import UTC, datetime
from typing import Any

from autox_tools.secrets._client import connect

_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _resolve_namespace(args: argparse.Namespace) -> str:
    """Return the target namespace from CLI flag or environment variable."""
    ns = args.namespace or os.getenv("RHOAI_PROJECT_NAME", "")
    if not ns:
        sys.exit("Namespace is required. Use --namespace/-n or set RHOAI_PROJECT_NAME.")
    return ns


def _decode_secret_data(data: dict[str, str] | None) -> dict[str, str]:
    """Base64-decode all values in a Kubernetes secret's ``.data`` dict."""
    if not data:
        return {}
    decoded: dict[str, str] = {}
    for key, value in data.items():
        try:
            decoded[key] = base64.b64decode(value).decode("utf-8", errors="replace")
        except Exception:
            decoded[key] = f"(decode error: {value[:40]})"
    return decoded


def _format_age(created: Any) -> str:
    """Format a creation timestamp as a human-readable age string."""
    if created is None:
        return "?"
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return "?"
    try:
        delta = datetime.now(UTC) - created
    except TypeError:
        return "?"
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0s"
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _exit_k8s_error(exc: Exception, namespace: str, action: str) -> None:
    """Exit with an actionable K8S API error message."""
    exc_str = str(exc)
    if "403" in exc_str or "Forbidden" in exc_str:
        sys.exit(
            f"K8S API returned 403 Forbidden for namespace '{namespace}'.\n"
            f"The token (RHOAI_TOKEN) lacks permission to {action} secrets.\n"
            "Verify that the token's service account has the required RBAC "
            "permissions for secrets in this namespace."
        )
    if "404" in exc_str or "Not Found" in exc_str:
        sys.exit(f"Secret not found in namespace '{namespace}'.")
    if "409" in exc_str or "Conflict" in exc_str or "AlreadyExists" in exc_str:
        sys.exit("The secret was modified by another process. Please retry the operation.")
    sys.exit(f"K8S API error: {exc}")


def _parse_key_value(literal: str) -> tuple[str, str]:
    """Parse a ``KEY=VALUE`` string, validating the key name."""
    if "=" not in literal:
        sys.exit(f"Invalid literal format: '{literal}'. Expected KEY=VALUE.")
    key, value = literal.split("=", 1)
    if not key:
        sys.exit(f"Invalid literal format: '{literal}'. Key cannot be empty.")
    if not _KEY_PATTERN.match(key):
        sys.exit(f"Invalid secret key name: '{key}'. Keys must contain only alphanumeric characters, '-', '_', or '.'.")
    return key, value


def _parse_labels(labels_str: str) -> dict[str, str]:
    """Parse a comma-separated ``KEY=VALUE,...`` label string."""
    result: dict[str, str] = {}
    for part in labels_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            sys.exit(f"Invalid label format: '{part}'. Expected KEY=VALUE.")
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _load_env_file(path: str) -> dict[str, str]:
    """Load key-value pairs from a dotenv-format file."""
    if not os.path.isfile(path):
        sys.exit(f"File not found: {path}")

    pairs: dict[str, str] = {}
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                sys.exit(f"Invalid format at {path}:{lineno} -- expected KEY=VALUE.")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                sys.exit(f"Empty key at {path}:{lineno}.")
            if not _KEY_PATTERN.match(key):
                sys.exit(
                    f"Invalid key name '{key}' at {path}:{lineno}. "
                    "Keys must contain only alphanumeric characters, '-', '_', or '.'."
                )
            pairs[key] = value
    return pairs


def _confirm(prompt: str) -> bool:
    """Prompt user for yes/no confirmation. Returns True on 'y'."""
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer == "y"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list(api: Any, args: argparse.Namespace, namespace: str) -> None:
    """List Opaque secrets in a namespace."""
    kwargs: dict[str, Any] = {"namespace": namespace, "_request_timeout": 30}
    if args.labels:
        kwargs["label_selector"] = args.labels

    try:
        secret_list = api.list_namespaced_secret(**kwargs)
    except Exception as exc:
        _exit_k8s_error(exc, namespace, "list")

    items = secret_list.items or []
    secrets = [s for s in items if (s.type or "") == "Opaque"]

    if args.filter:
        pattern = args.filter.lower()
        secrets = [s for s in secrets if pattern in (s.metadata.name or "").lower()]

    if args.json:
        rows = []
        for s in secrets:
            keys = sorted(s.data.keys()) if s.data else []
            labels = dict(s.metadata.labels) if s.metadata.labels else {}
            rows.append({
                "name": s.metadata.name,
                "keys": keys,
                "created": str(s.metadata.creation_timestamp),
                "labels": labels,
            })
        _print_json({"namespace": namespace, "total": len(rows), "secrets": rows})
        return

    if not secrets:
        print(f"No Opaque secrets found in namespace '{namespace}'.")
        return

    entries: list[tuple[str, str, str]] = []
    for s in secrets:
        name = s.metadata.name or ""
        key_count = str(len(s.data)) if s.data else "0"
        age = _format_age(s.metadata.creation_timestamp)
        entries.append((name, key_count, age))

    col_name = max(len("Name"), max(len(e[0]) for e in entries))
    col_keys = max(len("Keys"), max(len(e[1]) for e in entries))
    col_age = max(len("Age"), max(len(e[2]) for e in entries))

    print(f"  {'Name':<{col_name}}  {'Keys':>{col_keys}}  {'Age':>{col_age}}")
    print(f"  {'─' * col_name}  {'─' * col_keys}  {'─' * col_age}")
    for name, key_count, age in entries:
        print(f"  {name:<{col_name}}  {key_count:>{col_keys}}  {age:>{col_age}}")

    print(f"\n  {len(entries)} secret(s) in '{namespace}'")


def cmd_reveal(api: Any, args: argparse.Namespace, namespace: str) -> None:
    """Decode and display a secret's data."""
    try:
        secret = api.read_namespaced_secret(name=args.name, namespace=namespace, _request_timeout=30)
    except Exception as exc:
        _exit_k8s_error(exc, namespace, "read")

    if (secret.type or "") != "Opaque":
        sys.exit(f"Secret '{args.name}' is of type '{secret.type}', not Opaque.")

    decoded = _decode_secret_data(secret.data)
    labels = dict(secret.metadata.labels) if secret.metadata.labels else {}

    if args.json:
        _print_json({
            "name": args.name,
            "namespace": namespace,
            "created": str(secret.metadata.creation_timestamp),
            "labels": labels,
            "data": decoded,
        })
        return

    print(f"Secret    : {args.name}")
    print(f"Namespace : {namespace}")
    print(f"Created   : {secret.metadata.creation_timestamp}")
    if labels:
        label_str = ", ".join(f"{k}={v}" for k, v in sorted(labels.items()))
        print(f"Labels    : {label_str}")

    if not decoded:
        print("\n  (no data)")
        return

    print(f"\nData ({len(decoded)} key(s)):")
    max_key = max(len(k) for k in decoded)
    for key in sorted(decoded):
        print(f"  {key:<{max_key}}  : {decoded[key]}")


def cmd_create(api: Any, args: argparse.Namespace, namespace: str) -> None:
    """Create a new Opaque secret."""
    if not args.from_literal and not args.from_env_file:
        sys.exit("At least one of --from-literal or --from-env-file is required.")

    pairs: dict[str, str] = {}

    if args.from_env_file:
        pairs.update(_load_env_file(args.from_env_file))

    for lit in args.from_literal or []:
        key, value = _parse_key_value(lit)
        pairs[key] = value

    if not pairs:
        sys.exit("No key-value pairs provided.")

    encoded_data = {k: base64.b64encode(v.encode()).decode() for k, v in pairs.items()}

    labels: dict[str, str] | None = None
    if args.labels:
        labels = _parse_labels(args.labels)

    if not args.yes:
        key_list = ", ".join(sorted(pairs.keys()))
        if not _confirm(f"Create secret '{args.name}' with {len(pairs)} key(s) [{key_list}] in '{namespace}'?"):
            print("Aborted.")
            return

    from kubernetes import client as k8s_client

    metadata = k8s_client.V1ObjectMeta(name=args.name, namespace=namespace, labels=labels)
    body = k8s_client.V1Secret(metadata=metadata, data=encoded_data, type="Opaque")

    try:
        api.create_namespaced_secret(namespace=namespace, body=body)
    except Exception as exc:
        exc_str = str(exc)
        if "409" in exc_str or "Conflict" in exc_str or "AlreadyExists" in exc_str:
            sys.exit(f"Secret '{args.name}' already exists in '{namespace}'. Use 'edit' to update it.")
        _exit_k8s_error(exc, namespace, "create")

    if args.json:
        _print_json({"name": args.name, "namespace": namespace, "keys": sorted(pairs.keys()), "created": True})
        return

    print(f"Secret '{args.name}' created in '{namespace}' with {len(pairs)} key(s).")


def cmd_edit(api: Any, args: argparse.Namespace, namespace: str) -> None:
    """Update an existing secret."""
    if not args.set_values and not args.remove_keys:
        sys.exit("At least one of --set or --remove is required.")

    try:
        secret = api.read_namespaced_secret(name=args.name, namespace=namespace, _request_timeout=30)
    except Exception as exc:
        exc_str = str(exc)
        if "404" in exc_str or "Not Found" in exc_str:
            sys.exit(f"Secret '{args.name}' not found in '{namespace}'. Use 'create' to make a new one.")
        _exit_k8s_error(exc, namespace, "read")

    if (secret.type or "") != "Opaque":
        sys.exit(f"Secret '{args.name}' is of type '{secret.type}', not Opaque.")

    existing_data: dict[str, str] = dict(secret.data) if secret.data else {}
    existing_keys = set(existing_data.keys())

    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []

    for lit in args.set_values or []:
        key, value = _parse_key_value(lit)
        encoded = base64.b64encode(value.encode()).decode()
        if key in existing_keys:
            updated.append(key)
        else:
            added.append(key)
        existing_data[key] = encoded

    for key in args.remove_keys or []:
        if key in existing_data:
            del existing_data[key]
            removed.append(key)
        else:
            print(f"Warning: key '{key}' not found in secret, skipping removal.")

    if not added and not updated and not removed:
        print("No changes to apply.")
        return

    if not args.yes:
        changes: list[str] = []
        if added:
            changes.append(f"Adding: {', '.join(sorted(added))}")
        if updated:
            changes.append(f"Updating: {', '.join(sorted(updated))}")
        if removed:
            changes.append(f"Removing: {', '.join(sorted(removed))}")
        summary = "; ".join(changes)
        if not _confirm(f"Edit secret '{args.name}' in '{namespace}'? ({summary})"):
            print("Aborted.")
            return

    secret.data = existing_data

    try:
        api.replace_namespaced_secret(name=args.name, namespace=namespace, body=secret)
    except Exception as exc:
        _exit_k8s_error(exc, namespace, "update")

    final_keys = sorted(existing_data.keys())

    if args.json:
        _print_json({
            "name": args.name,
            "namespace": namespace,
            "added": sorted(added),
            "updated": sorted(updated),
            "removed": sorted(removed),
            "keys": final_keys,
        })
        return

    if added:
        print(f"  Added   : {', '.join(sorted(added))}")
    if updated:
        print(f"  Updated : {', '.join(sorted(updated))}")
    if removed:
        print(f"  Removed : {', '.join(sorted(removed))}")
    print(f"\nSecret '{args.name}' updated in '{namespace}' ({len(final_keys)} key(s)).")


def cmd_delete(api: Any, args: argparse.Namespace, namespace: str) -> None:
    """Delete a secret from the namespace."""
    try:
        secret = api.read_namespaced_secret(name=args.name, namespace=namespace, _request_timeout=30)
    except Exception as exc:
        exc_str = str(exc)
        if "404" in exc_str or "Not Found" in exc_str:
            sys.exit(f"Secret '{args.name}' not found in '{namespace}'.")
        _exit_k8s_error(exc, namespace, "read")

    if (secret.type or "") != "Opaque":
        sys.exit(f"Secret '{args.name}' is of type '{secret.type}', not Opaque. Refusing to delete.")

    key_count = len(secret.data) if secret.data else 0

    if not args.yes and not _confirm(f"Delete secret '{args.name}' ({key_count} key(s)) from '{namespace}'?"):
        print("Aborted.")
        return

    try:
        api.delete_namespaced_secret(name=args.name, namespace=namespace, _request_timeout=30)
    except Exception as exc:
        _exit_k8s_error(exc, namespace, "delete")

    if args.json:
        _print_json({"name": args.name, "namespace": namespace, "deleted": True})
        return

    print(f"Secret '{args.name}' deleted from '{namespace}'.")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secrets",
        description="Manage Kubernetes Opaque secrets on OpenShift AI clusters.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("-n", "--namespace", help="Target namespace (default: RHOAI_PROJECT_NAME env var)")

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p = sub.add_parser("list", help="List Opaque secrets in a namespace")
    p.add_argument("--filter", help="Substring filter on secret names (case-insensitive)")
    p.add_argument("--labels", help="Kubernetes label selector (e.g. 'app=myapp,env=prod')")

    # reveal
    p = sub.add_parser("reveal", help="Decode and display a secret's data")
    p.add_argument("name", help="Secret name")

    # create
    p = sub.add_parser("create", help="Create a new Opaque secret")
    p.add_argument("name", help="Secret name")
    p.add_argument("--from-literal", action="append", metavar="KEY=VALUE", help="Set a key-value pair (repeatable)")
    p.add_argument("--from-env-file", metavar="FILE", help="Load key-value pairs from a dotenv-format file")
    p.add_argument("--labels", help="Labels to apply (e.g. 'app=myapp,env=prod')")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # delete
    p = sub.add_parser("delete", help="Delete a secret")
    p.add_argument("name", help="Secret name")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    # edit
    p = sub.add_parser("edit", help="Update an existing secret")
    p.add_argument("name", help="Secret name")
    p.add_argument(
        "--set", action="append", metavar="KEY=VALUE", dest="set_values", help="Add or update a key (repeatable)",
    )
    p.add_argument("--remove", action="append", metavar="KEY", dest="remove_keys", help="Remove a key (repeatable)")
    p.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    api = connect()
    namespace = _resolve_namespace(args)

    commands: dict[str, Any] = {
        "list": cmd_list,
        "reveal": cmd_reveal,
        "create": cmd_create,
        "delete": cmd_delete,
        "edit": cmd_edit,
    }
    commands[args.command](api, args, namespace)


if __name__ == "__main__":
    main()
