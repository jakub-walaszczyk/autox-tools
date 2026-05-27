"""CLI entry point for OGX gateway management commands.

Usage::

    uv run ogx models [--type {all,llm,embedding,rerank}] [--metadata] [--json]
    uv run ogx info <model-id> [--json]
    uv run ogx providers [--json]
    uv run ogx stores [--json]
    uv run ogx health [--json]
    uv run ogx check [model-id] [--type {all,llm,embedding,rerank}] [--prompt TEXT] [--input TEXT] [--json]
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


def _compact_metadata(meta: dict[str, object] | None, max_width: int = 60) -> str:
    """Render a metadata dict as a truncated ``key=val, ...`` string."""
    if not meta:
        return "—"
    parts = [f"{k}={v}" for k, v in sorted(meta.items())]
    text = ", ".join(parts)
    if len(text) > max_width:
        return text[: max_width - 1] + "…"
    return text


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

    show_meta = getattr(args, "metadata", False)

    max_id = max(len("Model ID"), max(len(m.id) for m in models))
    max_type = max(len("Type"), max(len(_model_type(m)) for m in models))
    max_prov = max(len("Provider"), max(len(getattr(m, "provider_id", "") or "—") for m in models))

    header = f"  {'Model ID':<{max_id}}   {'Type':<{max_type}}   {'Provider':<{max_prov}}   {'Created':<16}"
    sep = f"  {'─' * max_id}   {'─' * max_type}   {'─' * max_prov}   {'─' * 16}"
    if show_meta:
        header += "   Metadata"
        sep += "   " + "─" * 8
    print(header)
    print(sep)
    for m in models:
        mid = m.id
        mtype = _model_type(m)
        prov = getattr(m, "provider_id", None) or "—"
        created = _format_ts(m.created)
        row = f"  {mid:<{max_id}}   {mtype:<{max_type}}   {prov:<{max_prov}}   {created:<16}"
        if show_meta:
            row += "   " + _compact_metadata(getattr(m, "metadata", None))
        print(row)

    print(f"\n  {len(models)} model(s)")


def cmd_info(client: OgxClient, args: argparse.Namespace) -> None:
    """Show detailed information and metadata for a single model."""
    model_id: str = args.model_id

    try:
        model = client.models.retrieve(model_id)
    except Exception as exc:
        sys.exit(f"Model '{model_id}' not found: {exc}")

    mid = getattr(model, "id", None) or getattr(model, "name", None) or model_id
    display_name = getattr(model, "display_name", None)
    mtype = _model_type(model)
    provider = getattr(model, "provider_id", None)
    provider_res = getattr(model, "provider_resource_id", None)
    owned_by = getattr(model, "owned_by", None)
    created = getattr(model, "created", None)
    created_at = getattr(model, "created_at", None)
    description = getattr(model, "description", None)
    max_input = getattr(model, "max_input_tokens", None)
    max_out = getattr(model, "max_tokens", None)
    meta: dict[str, object] | None = getattr(model, "metadata", None)

    if args.json:
        data: dict[str, object] = {"id": mid, "model_type": mtype}
        if display_name:
            data["display_name"] = display_name
        if provider:
            data["provider_id"] = provider
        if provider_res:
            data["provider_resource_id"] = provider_res
        if owned_by:
            data["owned_by"] = owned_by
        if created is not None:
            data["created"] = created
        if created_at:
            data["created_at"] = created_at
        if description:
            data["description"] = description
        if max_input is not None:
            data["max_input_tokens"] = max_input
        if max_out is not None:
            data["max_tokens"] = max_out
        if meta:
            data["metadata"] = meta
        _print_json(data)
        return

    fields: list[tuple[str, str]] = [("Model", mid)]
    if display_name:
        fields.append(("Display name", display_name))
    if mtype != "unknown":
        fields.append(("Type", mtype))
    if provider:
        fields.append(("Provider", provider))
    if provider_res:
        fields.append(("Resource ID", provider_res))
    if owned_by:
        fields.append(("Owned by", owned_by))
    if created is not None:
        fields.append(("Created", _format_ts(created)))
    if created_at:
        fields.append(("Created at", str(created_at)))
    if description:
        fields.append(("Description", description))
    if max_input is not None:
        fields.append(("Max input tokens", f"{max_input:,}"))
    if max_out is not None:
        fields.append(("Max output tokens", f"{max_out:,}"))

    label_w = max(len(label) for label, _ in fields)
    for label, value in fields:
        print(f"  {label:<{label_w}} : {value}")

    if meta:
        meta_w = max(len(k) for k in meta)
        print("  Metadata")
        for k in sorted(meta):
            print(f"    {k:<{meta_w}} : {meta[k]}")


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


def _run_check(
    client: OgxClient, model_id: str, mtype: str, provider: str,
    prompt: str, input_text: str,
) -> dict[str, object]:
    """Execute a sanity check for a single model and return the result dict."""
    result: dict[str, object] = {
        "model_id": model_id,
        "model_type": mtype,
        "provider_id": provider,
    }
    if mtype == "llm":
        result["prompt"] = prompt
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": prompt}],
            )
            result["status"] = "pass"
            result["response"] = completion.choices[0].message.content or ""
        except Exception as exc:
            result["status"] = "fail"
            result["error"] = str(exc)
    elif mtype == "embedding":
        result["input"] = input_text
        try:
            response = client.embeddings.create(model=model_id, input=input_text)
            embedding = response.data[0].embedding
            result["status"] = "pass"
            result["dimensions"] = len(embedding) if isinstance(embedding, list) else 0
        except Exception as exc:
            result["status"] = "fail"
            result["error"] = str(exc)
    elif mtype == "rerank":
        result["status"] = "skipped"
        result["message"] = "Sanity check is not supported for rerank models."
    else:
        result["status"] = "error"
        result["message"] = f"Unknown model type '{mtype}'."
    return result


def _print_check_result(result: dict[str, object]) -> None:
    """Print a single check result in human-readable form."""
    mtype = result["model_type"]
    status = str(result["status"]).upper()

    if mtype == "embedding":
        print(f"Model      : {result['model_id']}")
        print(f"Type       : {mtype}")
        print(f"Provider   : {result['provider_id']}")
        print(f"Input      : {result.get('input', '')}")
        print(f"Status     : {status}")
        if result["status"] == "pass":
            print(f"Dimensions : {result.get('dimensions', 0)}")
        elif result["status"] == "fail":
            print(f"Error      : {result.get('error', '')}")
        else:
            print(f"Message    : {result.get('message', '')}")
    else:
        print(f"Model    : {result['model_id']}")
        print(f"Type     : {mtype}")
        print(f"Provider : {result['provider_id']}")
        if mtype == "llm":
            print(f"Prompt   : {result.get('prompt', '')}")
        print(f"Status   : {status}")
        if result["status"] == "pass":
            print(f"Response : {result.get('response', '')}")
        elif result["status"] == "fail":
            print(f"Error    : {result.get('error', '')}")
        else:
            print(f"Message  : {result.get('message', '')}")


def _print_check_summary(results: list[dict[str, object]]) -> None:
    """Print a summary table for multiple check results."""
    if not results:
        print("No models found.")
        return

    max_id = max(len("Model ID"), max(len(str(r["model_id"])) for r in results))
    max_type = max(len("Type"), max(len(str(r["model_type"])) for r in results))
    max_prov = max(len("Provider"), max(len(str(r["provider_id"])) for r in results))

    header = f"  {'Model ID':<{max_id}}   {'Type':<{max_type}}   {'Provider':<{max_prov}}   {'Status':<7}   Detail"
    print(header)
    print(f"  {'─' * max_id}   {'─' * max_type}   {'─' * max_prov}   {'─' * 7}   {'─' * 30}")

    for r in results:
        status = str(r["status"]).upper()
        detail = ""
        if r["status"] == "pass":
            if r["model_type"] == "embedding":
                detail = f"dimensions={r.get('dimensions', 0)}"
            else:
                resp = str(r.get("response", ""))
                detail = resp[:50] + ("…" if len(resp) > 50 else "")
        elif r["status"] == "fail":
            err = str(r.get("error", ""))
            detail = err[:50] + ("…" if len(err) > 50 else "")
        elif r["status"] == "skipped":
            detail = str(r.get("message", ""))

        print(
            f"  {str(r['model_id']):<{max_id}}   {str(r['model_type']):<{max_type}}"
            f"   {str(r['provider_id']):<{max_prov}}   {status:<7}   {detail}"
        )

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errored = sum(1 for r in results if r["status"] == "error")

    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped")
    if errored:
        parts.append(f"{errored} error")
    print(f"\n  {', '.join(parts)} ({len(results)} total)")


def cmd_check(client: OgxClient, args: argparse.Namespace) -> None:
    """Run a sanity check against one or all models."""
    model_id: str | None = getattr(args, "model_id", None)
    prompt: str = args.prompt
    input_text: str = args.input

    if model_id is not None:
        try:
            model = client.models.retrieve(model_id)
        except Exception as exc:
            sys.exit(f"Model '{model_id}' not found: {exc}")

        mtype = _model_type(model)
        provider = getattr(model, "provider_id", None) or "—"

        if mtype not in ("llm", "embedding", "rerank"):
            sys.exit(f"Unknown model type '{mtype}' for model '{model_id}'. Cannot run sanity check.")

        result = _run_check(client, model_id, mtype, provider, prompt, input_text)
        if args.json:
            _print_json(result)
        else:
            _print_check_result(result)
        return

    # All-models mode
    response = client.models.list()
    raw_data = getattr(response, "data", None)
    if raw_data is None:
        raw_data = getattr(response, "models", [])
    models = sorted(raw_data, key=lambda m: m.id)

    type_filter: str = getattr(args, "type", "all")
    if type_filter != "all":
        models = [m for m in models if _model_type(m) == type_filter]

    results: list[dict[str, object]] = []
    for m in models:
        mtype = _model_type(m)
        provider = getattr(m, "provider_id", None) or "—"
        results.append(_run_check(client, m.id, mtype, provider, prompt, input_text))

    if args.json:
        _print_json({"total": len(results), "results": results})
    else:
        _print_check_summary(results)


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
    p.add_argument(
        "--metadata", "-m",
        action="store_true",
        help="Show metadata column in table output",
    )

    # info
    p = sub.add_parser("info", help="Show detailed model information and metadata")
    p.add_argument("model_id", help="Model ID to inspect")

    # providers
    sub.add_parser("providers", help="List vector store providers")

    # stores
    sub.add_parser("stores", help="List registered vector stores")

    # health
    sub.add_parser("health", help="Check OGX gateway health and version")

    # check
    p = sub.add_parser("check", help="Run a sanity check against one or all models")
    p.add_argument("model_id", nargs="?", default=None, help="Model ID to test (omit to check all)")
    p.add_argument(
        "--type", "-t",
        choices=["all", "llm", "embedding", "rerank"],
        default="all",
        help="Filter by model type when checking all models (default: all)",
    )
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
        "info": cmd_info,
        "providers": cmd_providers,
        "stores": cmd_stores,
        "health": cmd_health,
        "check": cmd_check,
    }
    commands[args.command](client, args)


if __name__ == "__main__":
    main()
