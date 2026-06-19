# `ogx` -- OGX Gateway CLI

Inspect and test models, providers, and vector stores on an OGX gateway.

## Setup

Set the following environment variables (or add them to a `.env` file):

| Variable | Required | Default | Description |
|---|---|---|---|
| `OGX_CLIENT_BASE_URL` | yes | -- | OGX server base URL |
| `OGX_CLIENT_API_KEY` | no | -- | API key for authentication |

Verify connectivity:

```bash
uv run ogx health
```

## Commands

### `models` -- List available models

```bash
uv run ogx models                     # all models
uv run ogx models --type llm          # LLMs only
uv run ogx models --type embedding    # embedding models only
uv run ogx models --type rerank       # rerank models only
uv run ogx models --metadata          # include metadata column (context_length, etc.)
uv run ogx --json models              # JSON output (always includes metadata)
```

### `info` -- Show detailed model information

```bash
uv run ogx info <model-id>            # full detail view with metadata
uv run ogx --json info <model-id>     # JSON output
```

Retrieves a single model and displays all available fields including
registration metadata (context length, embedding dimension, etc.) and
provider-specific fields (max input/output tokens, display name, description).

### `providers` -- List vector store providers

```bash
uv run ogx providers                  # filtered to vector store providers
uv run ogx --json providers           # JSON output
```

### `vs` -- Manage registered vector stores

List all registered vector stores:

```bash
uv run ogx vs                         # list all (default action)
uv run ogx vs list                    # explicit list
uv run ogx --json vs list             # JSON output
```

Delete vector stores by name/ID pattern or all at once:

```bash
uv run ogx vs delete "test-store.*"          # delete matching stores (interactive confirm)
uv run ogx vs delete "test-store.*" --yes    # skip confirmation
uv run ogx vs delete --all                   # delete all stores (interactive confirm)
uv run ogx vs delete --all --yes             # delete all, skip confirmation
uv run ogx vs delete "test.*" --dry-run      # preview what would be deleted
```

### `health` -- Check gateway health

```bash
uv run ogx health                     # status and version
uv run ogx --json health              # JSON output
```

### `check` -- Model sanity check

```bash
uv run ogx check                                     # check all models
uv run ogx check --type llm                          # check all LLM models only
uv run ogx check --type embedding                    # check all embedding models only
uv run ogx check <model-id>                          # check a single model
uv run ogx check <model-id> --prompt "Say hello."    # custom LLM prompt
uv run ogx check <model-id> --input "Custom text."   # custom embedding input
uv run ogx --json check                              # JSON output (all models)
```

When called without a `model-id`, iterates over all registered models and
prints a summary table with per-model pass/fail status. Use `--type` to
narrow the sweep to a single model type.

When called with a `model-id`, sends a single request and reports the result:

- **LLM models**: sends a chat completion request and checks for a non-empty response.
- **Embedding models**: sends an embedding request and reports the output dimensions.
- **Rerank models**: reports that sanity checks are not supported.

## Architecture

```
autox_tools/ogx/
    __init__.py     # package marker
    _client.py      # OgxClient connection factory (env vars)
    cli.py          # argparse entry point and command handlers
    README.md       # this file
```
