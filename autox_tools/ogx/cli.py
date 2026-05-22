"""CLI entry point for OGX gateway management commands.

Usage::

    uv run ogx models [--type {all,llm,embedding,rerank}] [--json]
    uv run ogx providers [--json]
    uv run ogx stores [--json]
    uv run ogx health [--json]
    uv run ogx check <model-id> [--prompt TEXT] [--input TEXT] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from autox_tools.ogx._client import connect

if TYPE_CHECKING:
    from ogx_client import OgxClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _format_ts(unix_ts: int | float | None) -> str:
    """Format a Unix timestamp as a human-readable UTC string."""
    if unix_ts is None:
        return "—"
    return datetime.fromtimestamp(int(unix_ts), tz=UTC).strftime("%Y-%m-%d %H:%M")


_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def _human_size(nbytes: int | None) -> str:
    """Format byte count as a human-readable string."""
    if nbytes is None:
        return "—"
    size = float(nbytes)
    for unit in _SIZE_UNITS[:-1]:
        if abs(size) < 1024.0:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024.0
    return f"{size:.1f} {_SIZE_UNITS[-1]}"


def _model_type(model: object) -> str:
    """Extract model_type from a Model object.

    The list and retrieve endpoints return different Model classes:
    list uses a ``model_type`` property over ``custom_metadata``, while
    retrieve stores it in ``api_model_type`` (Pydantic alias ``model_type``).
    """
    for attr in ("model_type", "api_model_type"):
        val = getattr(model, attr, None)
        if val and isinstance(val, str):
            return val
    return "unknown"


def _health_status(health: object) -> str:
    """Extract a concise health status string from a provider health dict."""
    if isinstance(health, dict):
        return str(health.get("status", "unknown"))
    return "unknown"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_models(client: OgxClient, args: argparse.Namespace) -> None:
    """List available models, optionally filtered by type."""
    response = client.models.list()
    raw_data = getattr(response, "data", None)
    if raw_data is None:
        raw_data = getattr(response, "models", [])
    models = sorted(raw_data, key=lambda m: m.id)

    if args.type != "all":
        models = [m for m in models if _model_type(m) == args.type]

    if args.json:
        _print_json({
            "total": len(models),
            "models": [
                {
                    "id": m.id,
                    "model_type": _model_type(m),
                    "provider_id": getattr(m, "provider_id", None),
                    "provider_resource_id": getattr(m, "provider_resource_id", None),
                    "owned_by": m.owned_by,
                    "created": m.created,
                    "metadata": getattr(m, "metadata", None),
                }
                for m in models
            ],
        })
        return

    if not models:
        print("No models found.")
        return

    max_id = max(len("Model ID"), max(len(m.id) for m in models))
    max_type = max(len("Type"), max(len(_model_type(m)) for m in models))
    max_prov = max(len("Provider"), max(len(getattr(m, "provider_id", "") or "—") for m in models))

    header = f"  {'Model ID':<{max_id}}   {'Type':<{max_type}}   {'Provider':<{max_prov}}   Created"
    print(header)
    print(f"  {'─' * max_id}   {'─' * max_type}   {'─' * max_prov}   {'─' * 16}")
    for m in models:
        mid = m.id
        mtype = _model_type(m)
        prov = getattr(m, "provider_id", None) or "—"
        created = _format_ts(m.created)
        print(f"  {mid:<{max_id}}   {mtype:<{max_type}}   {prov:<{max_prov}}   {created}")

    print(f"\n  {len(models)} model(s)")


def cmd_providers(client: OgxClient, args: argparse.Namespace) -> None:
    """List providers that serve vector store operations."""
    all_providers = client.providers.list()
    providers = sorted(
        [p for p in all_providers if "vector" in p.api.lower()],
        key=lambda p: p.provider_id,
    )

    if args.json:
        _print_json({
            "total": len(providers),
            "providers": [
                {
                    "provider_id": p.provider_id,
                    "provider_type": p.provider_type,
                    "api": p.api,
                    "config": p.config,
                    "health": p.health,
                }
                for p in providers
            ],
        })
        return

    if not providers:
        print("No vector store providers found.")
        return

    max_pid = max(len("Provider ID"), max(len(p.provider_id) for p in providers))
    max_pt = max(len("Type"), max(len(p.provider_type) for p in providers))
    max_api = max(len("API"), max(len(p.api) for p in providers))

    header = f"  {'Provider ID':<{max_pid}}   {'Type':<{max_pt}}   {'API':<{max_api}}   Health"
    print(header)
    print(f"  {'─' * max_pid}   {'─' * max_pt}   {'─' * max_api}   {'─' * 10}")
    for p in providers:
        hs = _health_status(p.health)
        print(f"  {p.provider_id:<{max_pid}}   {p.provider_type:<{max_pt}}   {p.api:<{max_api}}   {hs}")

    print(f"\n  {len(providers)} provider(s)")


def cmd_stores(client: OgxClient, args: argparse.Namespace) -> None:
    """List registered vector stores."""
    response = client.vector_stores.list()
    stores = sorted(response.data, key=lambda vs: vs.name or vs.id)

    if args.json:
        _print_json({
            "total": len(stores),
            "vector_stores": [
                {
                    "id": vs.id,
                    "name": vs.name,
                    "status": vs.status,
                    "file_counts": {
                        "completed": vs.file_counts.completed,
                        "in_progress": vs.file_counts.in_progress,
                        "failed": vs.file_counts.failed,
                        "cancelled": vs.file_counts.cancelled,
                        "total": vs.file_counts.total,
                    } if vs.file_counts else None,
                    "usage_bytes": vs.usage_bytes,
                    "created_at": vs.created_at,
                    "expires_at": vs.expires_at,
                    "last_active_at": vs.last_active_at,
                    "metadata": vs.metadata,
                }
                for vs in stores
            ],
        })
        return

    if not stores:
        print("No vector stores found.")
        return

    max_name = max(len("Name"), max(len(vs.name or "—") for vs in stores))
    max_sid = max(len("ID"), max(len(vs.id) for vs in stores))
    max_status = max(len("Status"), max(len(vs.status) for vs in stores))

    header = (
        f"  {'Name':<{max_name}}   {'ID':<{max_sid}}   {'Status':<{max_status}}"
        f"   {'Files':>5}   {'Usage':>10}   Created"
    )
    print(header)
    sep = (
        f"  {'─' * max_name}   {'─' * max_sid}   {'─' * max_status}"
        f"   {'─' * 5}   {'─' * 10}   {'─' * 16}"
    )
    print(sep)
    for vs in stores:
        name = vs.name or "—"
        files = vs.file_counts.total if vs.file_counts else 0
        usage = _human_size(vs.usage_bytes)
        created = _format_ts(vs.created_at)
        print(
            f"  {name:<{max_name}}   {vs.id:<{max_sid}}   {vs.status:<{max_status}}"
            f"   {files:>5}   {usage:>10}   {created}"
        )

    print(f"\n  {len(stores)} vector store(s)")


def cmd_health(client: OgxClient, args: argparse.Namespace) -> None:
    """Check OGX gateway health and version."""
    health = client.inspect.health()

    try:
        version_info = client.inspect.version()
        version = version_info.version
    except Exception:
        version = "unknown"

    if args.json:
        _print_json({"status": health.status, "version": version})
        return

    print(f"Status  : {health.status}")
    print(f"Version : {version}")


def cmd_check(client: OgxClient, args: argparse.Namespace) -> None:
    """Run a sanity check against a model."""
    model_id: str = args.model_id

    try:
        model = client.models.retrieve(model_id)
    except Exception as exc:
        sys.exit(f"Model '{model_id}' not found: {exc}")

    mtype = _model_type(model)
    provider = getattr(model, "provider_id", None) or "—"

    if mtype == "llm":
        _check_llm(client, args, model_id, mtype, provider)
    elif mtype == "embedding":
        _check_embedding(client, args, model_id, mtype, provider)
    elif mtype == "rerank":
        if args.json:
            _print_json({
                "model_id": model_id,
                "model_type": mtype,
                "provider_id": provider,
                "status": "skipped",
                "message": "Sanity check is not supported for rerank models.",
            })
        else:
            print(f"Model    : {model_id}")
            print(f"Type     : {mtype}")
            print(f"Provider : {provider}")
            print("Status   : SKIPPED")
            print("Message  : Sanity check is not supported for rerank models.")
    else:
        sys.exit(f"Unknown model type '{mtype}' for model '{model_id}'. Cannot run sanity check.")


def _check_llm(
    client: OgxClient, args: argparse.Namespace, model_id: str, mtype: str, provider: str,
) -> None:
    """Send a chat completion request and report pass/fail."""
    prompt: str = args.prompt
    status = "pass"
    response_text = ""
    error = ""

    try:
        completion = client.chat.completions.create(
            model=model_id,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = completion.choices[0].message.content or ""
    except Exception as exc:
        status = "fail"
        error = str(exc)

    if args.json:
        result: dict[str, object] = {
            "model_id": model_id,
            "model_type": mtype,
            "provider_id": provider,
            "status": status,
            "prompt": prompt,
        }
        if status == "pass":
            result["response"] = response_text
        else:
            result["error"] = error
        _print_json(result)
        return

    print(f"Model    : {model_id}")
    print(f"Type     : {mtype}")
    print(f"Provider : {provider}")
    print(f"Prompt   : {prompt}")
    print(f"Status   : {'PASS' if status == 'pass' else 'FAIL'}")
    if status == "pass":
        print(f"Response : {response_text}")
    else:
        print(f"Error    : {error}")


def _check_embedding(
    client: OgxClient, args: argparse.Namespace, model_id: str, mtype: str, provider: str,
) -> None:
    """Send an embedding request and report pass/fail."""
    input_text: str = args.input
    status = "pass"
    dimensions = 0
    error = ""

    try:
        response = client.embeddings.create(model=model_id, input=input_text)
        embedding = response.data[0].embedding
        dimensions = len(embedding) if isinstance(embedding, list) else 0
    except Exception as exc:
        status = "fail"
        error = str(exc)

    if args.json:
        result: dict[str, object] = {
            "model_id": model_id,
            "model_type": mtype,
            "provider_id": provider,
            "status": status,
            "input": input_text,
        }
        if status == "pass":
            result["dimensions"] = dimensions
        else:
            result["error"] = error
        _print_json(result)
        return

    print(f"Model      : {model_id}")
    print(f"Type       : {mtype}")
    print(f"Provider   : {provider}")
    print(f"Input      : {input_text}")
    print(f"Status     : {'PASS' if status == 'pass' else 'FAIL'}")
    if status == "pass":
        print(f"Dimensions : {dimensions}")
    else:
        print(f"Error      : {error}")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogx",
        description="Inspect and test models, providers, and vector stores on an OGX gateway.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # models
    p = sub.add_parser("models", help="List available models")
    p.add_argument(
        "--type", "-t",
        choices=["all", "llm", "embedding", "rerank"],
        default="all",
        help="Filter by model type (default: all)",
    )

    # providers
    sub.add_parser("providers", help="List vector store providers")

    # stores
    sub.add_parser("stores", help="List registered vector stores")

    # health
    sub.add_parser("health", help="Check OGX gateway health and version")

    # check
    p = sub.add_parser("check", help="Run a sanity check against a model")
    p.add_argument("model_id", help="Model ID to test")
    p.add_argument(
        "--prompt", "-p",
        default="What is 2+2? Reply with just the number.",
        help="Prompt for LLM sanity check (default: arithmetic question)",
    )
    p.add_argument(
        "--input", "-i",
        default="The quick brown fox jumps over the lazy dog.",
        help="Input text for embedding sanity check (default: pangram sentence)",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    client = connect()

    commands = {
        "models": cmd_models,
        "providers": cmd_providers,
        "stores": cmd_stores,
        "health": cmd_health,
        "check": cmd_check,
    }
    commands[args.command](client, args)


if __name__ == "__main__":
    main()
