# `maas` -- OpenShift MaaS CLI

List and sanity-check models served by OpenShift Model-as-a-Service (MaaS).

This tool is the MaaS counterpart to [`ogx`](../ogx/README.md); the two run in
parallel while support for OGX is phased out.

## How MaaS differs from OGX

MaaS exposes an OpenAI-compatible surface split across two kinds of endpoints:

- A **general** endpoint at `{base_url}/maas-api/v1` that serves the model
  *listing* only.
- A **per-model** endpoint at `{scheme}://{host}/{owned_by}/v1` that serves
  chat/embedding inference for a single model.

Because MaaS carries **no metadata distinguishing LLM from embedding models**,
`models` lists every deployed model without a type column, and `check` probes a
model to discover how it responds. Inference is served per-model, so `check`
first lists the models to derive each per-model URL from its `owned_by` prefix.

## Setup

### Profile-based configuration (recommended)

Define named MaaS configs in `.autox.yaml` and select them via CLI flags:

```bash
uv run maas -p dev models                    # use the "dev" profile
uv run maas -t dev-maas models               # target a named config directly
```

`base_url` is the **host root only** (no API path); the CLI appends
`/maas-api/v1` for listing and derives per-model endpoints automatically.

```yaml
maas:
  dev-maas:
    base_url: https://maas.apps.<cluster>.openshiftapps.com
    api_key: ${MAAS_API_KEY}
    verify_tls: true          # set false for self-signed cluster routes
```

See the [main README](../../README.md#configuration) for full details on
`.autox.yaml` profiles and resolution order. Run `uv run config init` to
generate a starter config.

### Environment variables

Alternatively, set the following environment variables (or add them to a `.env`
file). This path is used automatically when no `.autox.yaml` is present or no
profile/target is specified.

| Variable | Required | Default | Description |
|---|---|---|---|
| `MAAS_BASE_URL` | yes | -- | MaaS host root (no API path) |
| `MAAS_API_KEY` | no | -- | API key/token for authentication |
| `MAAS_VERIFY_TLS` | no | `true` | Set `false`/`0`/`no` to skip TLS verification |

## Commands

### `models` -- List available models

```bash
uv run maas models                    # short id, owner, created
uv run maas models --metadata         # include a column with MaaS-specific extra fields
uv run maas --json models             # JSON output (id, name, owner, and endpoint)
```

### `info` -- Show detailed model information

```bash
uv run maas info <model-id>           # short or fully-qualified id
uv run maas --json info <model-id>    # JSON output
```

Locates the model within the listing (MaaS has no per-model retrieve endpoint)
and prints its fully-qualified `id`, short `name`, `owned_by`, derived inference
endpoint, creation time, and any extra fields.

### `check` -- Model sanity check

```bash
uv run maas check                                    # probe all models
uv run maas check <model-id>                         # probe a single model
uv run maas check <model-id> --type llm              # probe as an LLM only
uv run maas check <model-id> --type embedding        # probe as an embedding model only
uv run maas check <model-id> --prompt "Say hello."   # custom LLM prompt
uv run maas check <model-id> --input "Custom text."  # custom embedding input
uv run maas --json check                             # JSON output (all models)
```

Because MaaS provides no type metadata, the default `--type auto` probes each
model as an LLM first (chat completion), then as an embedding model, reporting
the first modality that responds:

- **LLM**: sends a chat completion and checks for a non-empty response.
- **Embedding**: sends an embedding request and reports the output dimensions.
- **Neither**: reports a failure with both probe errors.

Use `--type llm` or `--type embedding` to restrict the probe to a single
modality when you already know a model's type.

## Troubleshooting

- **`HTTP 503: the deployment is unavailable`** — the OpenShift route resolves
  but has no ready backend pod (the MaaS deployment is down or scaled to zero).
  This is a cluster-side issue, not a CLI misconfiguration: confirm the MaaS
  service is running and that `base_url` points to a live deployment.
- **`Cannot reach MaaS ...`** — DNS/TLS/connection failure. Check `base_url`,
  VPN/network access, and set `verify_tls: false` for self-signed cluster routes.
- **`rejected the request (HTTP 401/403)`** — the token was missing or invalid;
  check `api_key` (or `MAAS_API_KEY`).

## Architecture

```
autox_tools/maas/
    __init__.py     # package marker
    _client.py      # settings resolution + OpenAI client / endpoint derivation
    cli.py          # argparse entry point and command handlers
    README.md       # this file
```
