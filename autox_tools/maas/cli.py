"""CLI entry point for OpenShift MaaS model management commands.

Usage::

    uv run maas models [--metadata] [--json]
    uv run maas info <model-id> [--json]
    uv run maas check [model-id] [--type {auto,llm,embedding}] [--prompt TEXT] [--input TEXT] [--json]

OpenShift MaaS carries no metadata distinguishing foundation (LLM) from
embedding models, so ``models`` lists every deployed model without a type
column and ``check`` probes each model to discover how it responds. Because
inference is served per-model at ``/{owned_by}/v1`` (not by the listing
endpoint), ``check`` first lists the models to derive each per-model URL.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn

from openai import APIConnectionError, APIError, APIStatusError

from autox_tools._output import print_json
from autox_tools.maas import _client

if TYPE_CHECKING:
    from openai import OpenAI

    from autox_tools.maas._client import MaasSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# OpenAI Model attributes surfaced explicitly; everything else is treated as extra.
_KNOWN_MODEL_FIELDS = ("id", "created", "object", "owned_by")


def _format_ts(unix_ts: int | float | None) -> str:
    """Format a Unix timestamp as a human-readable UTC string."""
    if unix_ts is None:
        return "—"
    try:
        return datetime.fromtimestamp(int(unix_ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return str(unix_ts)


def _short_id(model: object) -> str:
    """Return the short, callable model id (final path segment of the full id).

    MaaS lists models with fully-qualified ids such as
    ``publishers/ai-eng-cracow/models/qwen3-8b``; the last segment is the id
    used when calling chat/embeddings on the per-model endpoint.
    """
    full = str(getattr(model, "id", ""))
    return full.rsplit("/", 1)[-1]


def _owned_by(model: object) -> str:
    """Return the ``owned_by`` path prefix used to build the per-model endpoint.

    Falls back to deriving ``<namespace>/<short-id>`` from the full id
    (``publishers/<namespace>/models/<short-id>``) when ``owned_by`` is absent.
    """
    owned = getattr(model, "owned_by", None)
    if owned:
        return str(owned)
    parts = str(getattr(model, "id", "")).split("/")
    if len(parts) >= 4:
        return f"{parts[1]}/{parts[-1]}"
    return str(getattr(model, "id", ""))


def _model_dict(model: object) -> dict[str, Any]:
    """Return a plain dict of a model's fields, tolerant of pydantic or namespace objects."""
    for attr in ("model_dump", "to_dict"):
        method = getattr(model, attr, None)
        if callable(method):
            try:
                return dict(method())
            except TypeError:
                pass
    return {k: v for k, v in vars(model).items() if not k.startswith("_")}


def _extra_fields(model: object) -> dict[str, Any]:
    """Return model fields beyond the well-known OpenAI ones (MaaS-specific extras)."""
    return {k: v for k, v in _model_dict(model).items() if k not in _KNOWN_MODEL_FIELDS}


def _compact_metadata(meta: dict[str, Any] | None, max_width: int = 60) -> str:
    """Render a metadata dict as a truncated ``key=val, ...`` string."""
    if not meta:
        return "—"
    text = ", ".join(f"{k}={v}" for k, v in sorted(meta.items()))
    if len(text) > max_width:
        return text[: max_width - 1] + "…"
    return text


def _list_models(client: OpenAI) -> list:
    """List models from the general endpoint, sorted by short id."""
    response = client.models.list()
    data = getattr(response, "data", None)
    if data is None:
        data = list(response)  # OpenAI SyncPage is also directly iterable
    return sorted(data, key=_short_id)


def _find_model(models: list[object], model_id: str) -> object | None:
    """Locate a model by its short id or its fully-qualified id."""
    for model in models:
        if _short_id(model) == model_id or str(getattr(model, "id", "")) == model_id:
            return model
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_models(client: OpenAI, settings: MaasSettings, args: argparse.Namespace) -> None:
    """List models available in MaaS.

    MaaS exposes no type metadata, so results are not split by model type; use
    ``check`` to discover whether a model serves chat or embeddings.
    """
    models = _list_models(client)

    if args.json:
        print_json({
            "total": len(models),
            "models": [
                {
                    "id": str(getattr(m, "id", "")),
                    "name": _short_id(m),
                    "owned_by": _owned_by(m),
                    "created": getattr(m, "created", None),
                    "endpoint": _client.model_endpoint(settings.base_url, _owned_by(m)),
                    "extra": _extra_fields(m) or None,
                }
                for m in models
            ],
        })
        return

    if not models:
        print("No models found.")
        return

    show_meta = getattr(args, "metadata", False)

    max_id = max(len("Model ID"), max(len(_short_id(m)) for m in models))
    max_owner = max(len("Owned by"), max(len(_owned_by(m)) for m in models))

    header = f"  {'Model ID':<{max_id}}   {'Owned by':<{max_owner}}   {'Created':<16}"
    sep = f"  {'─' * max_id}   {'─' * max_owner}   {'─' * 16}"
    if show_meta:
        header += "   Metadata"
        sep += "   " + "─" * 8
    print(header)
    print(sep)
    for m in models:
        created = _format_ts(getattr(m, "created", None))
        row = f"  {_short_id(m):<{max_id}}   {_owned_by(m):<{max_owner}}   {created:<16}"
        if show_meta:
            row += "   " + _compact_metadata(_extra_fields(m))
        print(row)

    print(f"\n  {len(models)} model(s)")


def cmd_info(client: OpenAI, settings: MaasSettings, args: argparse.Namespace) -> None:
    """Show detailed information for a single model, including its inference endpoint.

    The model is located within the listing (MaaS has no per-model retrieve
    endpoint), matched by short id or fully-qualified id.
    """
    model_id: str = args.model_id
    models = _list_models(client)
    model = _find_model(models, model_id)
    if model is None:
        available = ", ".join(_short_id(m) for m in models) or "(none listed)"
        sys.exit(f"Model '{model_id}' not found in MaaS. Available: {available}")

    short = _short_id(model)
    full = str(getattr(model, "id", short))
    owned = _owned_by(model)
    endpoint = _client.model_endpoint(settings.base_url, owned)
    created = getattr(model, "created", None)
    extra = _extra_fields(model)

    if args.json:
        data: dict[str, Any] = {"id": full, "name": short, "owned_by": owned, "endpoint": endpoint}
        if created is not None:
            data["created"] = created
        if extra:
            data["extra"] = extra
        print_json(data)
        return

    fields: list[tuple[str, str]] = [
        ("ID", full),
        ("Name", short),
        ("Owned by", owned),
        ("Endpoint", endpoint),
    ]
    if created is not None:
        fields.append(("Created", _format_ts(created)))

    label_w = max(len(label) for label, _ in fields)
    for label, value in fields:
        print(f"  {label:<{label_w}} : {value}")

    if extra:
        extra_w = max(len(k) for k in extra)
        print("  Extra")
        for k in sorted(extra):
            print(f"    {k:<{extra_w}} : {extra[k]}")


def _probe_llm(mc: OpenAI, model_id: str, prompt: str) -> str:
    """Send a chat completion and return the response content (raises on failure)."""
    completion = mc.chat.completions.create(
        model=model_id,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=64,
    )
    return completion.choices[0].message.content or ""


def _probe_embedding(mc: OpenAI, model_id: str, text: str) -> int:
    """Send an embedding request and return the vector dimensionality (raises on failure)."""
    response = mc.embeddings.create(model=model_id, input=text)
    embedding = response.data[0].embedding
    return len(embedding) if isinstance(embedding, list) else 0


def _run_check(settings: MaasSettings, model: object, mode: str, prompt: str, input_text: str) -> dict[str, Any]:
    """Probe a single model and return a structured result.

    With ``mode="auto"`` the model is probed as an LLM first, then as an
    embedding model; the first successful modality wins. With ``mode="llm"`` or
    ``mode="embedding"`` only that modality is attempted.
    """
    short = _short_id(model)
    owned = _owned_by(model)
    result: dict[str, Any] = {
        "model_id": short,
        "owned_by": owned,
        "endpoint": _client.model_endpoint(settings.base_url, owned),
    }
    mc = _client.model_client(settings, owned)

    llm_err = emb_err = ""

    if mode in ("auto", "llm"):
        try:
            content = _probe_llm(mc, short, prompt)
            return {**result, "status": "pass", "detected_type": "llm", "response": content}
        except Exception as exc:  # surface any client/transport error as a failure
            llm_err = str(exc)
            if mode == "llm":
                return {**result, "status": "fail", "detected_type": "llm", "error": llm_err}

    if mode in ("auto", "embedding"):
        try:
            dims = _probe_embedding(mc, short, input_text)
            return {**result, "status": "pass", "detected_type": "embedding", "dimensions": dims}
        except Exception as exc:  # surface any client/transport error as a failure
            emb_err = str(exc)
            if mode == "embedding":
                return {**result, "status": "fail", "detected_type": "embedding", "error": emb_err}

    return {
        **result,
        "status": "fail",
        "detected_type": "unknown",
        "error": f"chat: {llm_err} | embeddings: {emb_err}",
    }


def _print_check_result(result: dict[str, Any]) -> None:
    """Print a single check result in human-readable form."""
    status = str(result["status"]).upper()
    print(f"Model    : {result['model_id']}")
    print(f"Owned by : {result['owned_by']}")
    print(f"Endpoint : {result['endpoint']}")
    print(f"Type     : {result.get('detected_type', 'unknown')}")
    print(f"Status   : {status}")
    if result["status"] == "pass":
        if result.get("detected_type") == "embedding":
            print(f"Dimensions : {result.get('dimensions', 0)}")
        else:
            print(f"Response : {result.get('response', '')}")
    else:
        print(f"Error    : {result.get('error', '')}")


def _print_check_summary(results: list[dict[str, Any]]) -> None:
    """Print a summary table for multiple check results."""
    if not results:
        print("No models found.")
        return

    max_id = max(len("Model ID"), max(len(str(r["model_id"])) for r in results))
    max_type = max(len("Type"), max(len(str(r.get("detected_type", "unknown"))) for r in results))

    header = f"  {'Model ID':<{max_id}}   {'Type':<{max_type}}   {'Status':<7}   Detail"
    print(header)
    print(f"  {'─' * max_id}   {'─' * max_type}   {'─' * 7}   {'─' * 30}")

    for r in results:
        status = str(r["status"]).upper()
        if r["status"] == "pass":
            if r.get("detected_type") == "embedding":
                detail = f"dimensions={r.get('dimensions', 0)}"
            else:
                resp = str(r.get("response", ""))
                detail = resp[:50] + ("…" if len(resp) > 50 else "")
        else:
            err = str(r.get("error", ""))
            detail = err[:50] + ("…" if len(err) > 50 else "")

        dtype = str(r.get("detected_type", "unknown"))
        print(f"  {r['model_id']!s:<{max_id}}   {dtype:<{max_type}}   {status:<7}   {detail}")

    passed = sum(1 for r in results if r["status"] == "pass")
    failed = sum(1 for r in results if r["status"] == "fail")
    parts = []
    if passed:
        parts.append(f"{passed} passed")
    if failed:
        parts.append(f"{failed} failed")
    print(f"\n  {', '.join(parts) or '0 checked'} ({len(results)} total)")


def cmd_check(client: OpenAI, settings: MaasSettings, args: argparse.Namespace) -> None:
    """Sanity-check one or all MaaS models by issuing a live inference request.

    Discovery (listing) always runs first so each model's per-model endpoint can
    be derived from its ``owned_by`` prefix.
    """
    models = _list_models(client)
    mode: str = args.type
    prompt: str = args.prompt
    input_text: str = args.input

    model_id: str | None = getattr(args, "model_id", None)
    if model_id is not None:
        model = _find_model(models, model_id)
        if model is None:
            available = ", ".join(_short_id(m) for m in models) or "(none listed)"
            sys.exit(f"Model '{model_id}' not found in MaaS. Available: {available}")
        result = _run_check(settings, model, mode, prompt, input_text)
        if args.json:
            print_json(result)
        else:
            _print_check_result(result)
        return

    results = [_run_check(settings, m, mode, prompt, input_text) for m in models]
    if args.json:
        print_json({"total": len(results), "results": results})
    else:
        _print_check_summary(results)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def _short_body(text: str, limit: int = 200) -> str:
    """Condense an error response body to a single, bounded line.

    MaaS routes return the OpenShift ingress router's HTML page (not JSON) when
    the backend is down; echoing that page verbatim is noise, so HTML bodies are
    collapsed to a marker and any other body is whitespace-normalized and clipped.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        return "(HTML error page)"
    collapsed = " ".join(stripped.split())
    return collapsed[:limit] + ("…" if len(collapsed) > limit else "")


def _abort_on_api_error(exc: APIError, settings: MaasSettings) -> NoReturn:
    """Translate an OpenAI SDK error into a concise, actionable fatal message.

    The raw SDK exception embeds the full HTTP response body (for MaaS, the
    OpenShift router's HTML error page) and a deep traceback — unhelpful to a CLI
    user. This distills the failure to the endpoint, HTTP status, and a
    remediation hint before exiting non-zero.
    """
    endpoint = _client.list_endpoint(settings.base_url)
    request = getattr(exc, "request", None)
    url = str(getattr(request, "url", "") or endpoint)

    if isinstance(exc, APIConnectionError):
        cause = exc.__cause__ or exc
        sys.exit(
            f"Cannot reach MaaS at {url}: {cause}. "
            f"Check base_url, network/VPN access, and verify_tls."
        )

    if isinstance(exc, APIStatusError):
        code = exc.response.status_code
        body = exc.response.text or ""
        if code in (502, 503, 504) or "Application is not available" in body:
            sys.exit(
                f"MaaS at {url} returned HTTP {code}: the deployment is unavailable "
                f"(the route has no ready backend). Verify the MaaS service is "
                f"running and that base_url points to a live deployment."
            )
        if code in (401, 403):
            sys.exit(f"MaaS at {url} rejected the request (HTTP {code}). Check api_key.")
        sys.exit(f"MaaS request to {url} failed with HTTP {code}: {_short_body(body)}")

    sys.exit(f"MaaS request failed: {exc}")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maas",
        description="List and sanity-check models served by OpenShift MaaS.",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    # models
    p = sub.add_parser("models", help="List available models")
    p.add_argument(
        "--metadata", "-m",
        action="store_true",
        help="Show a metadata column with any MaaS-specific extra fields",
    )

    # info
    p = sub.add_parser("info", help="Show detailed model information and inference endpoint")
    p.add_argument("model_id", help="Model ID (short or fully-qualified) to inspect")

    # check
    p = sub.add_parser("check", help="Run a sanity check against one or all models")
    p.add_argument("model_id", nargs="?", default=None, help="Model ID to test (omit to check all)")
    p.add_argument(
        "--type", "-t",
        choices=["auto", "llm", "embedding"],
        default="auto",
        help="Probe as a specific modality; 'auto' tries chat then embeddings (default: auto)",
    )
    p.add_argument(
        "--prompt", "-p",
        default="What is 2+2? Reply with just the number.",
        help="Prompt for the LLM probe (default: arithmetic question)",
    )
    p.add_argument(
        "--input", "-i",
        default="The quick brown fox jumps over the lazy dog.",
        help="Input text for the embedding probe (default: pangram sentence)",
    )

    return parser


def main() -> None:
    parser = _build_parser()

    from autox_tools.config._loader import add_profile_args, resolve
    add_profile_args(parser, target=True)

    args = parser.parse_args()
    cfg = resolve("maas", args)
    settings = _client.resolve_settings(cfg)
    client = _client.connect(settings)

    commands = {
        "models": cmd_models,
        "info": cmd_info,
        "check": cmd_check,
    }
    try:
        commands[args.command](client, settings, args)
    except APIError as exc:
        _abort_on_api_error(exc, settings)


if __name__ == "__main__":
    main()
