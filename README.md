# autox-tools

CLI tooling for operators and developers working with **AutoRAG** and **AutoML** features in [Red Hat OpenShift AI](https://www.redhat.com/en/blog/introducing-auto-ml-and-auto-rag-guided-experience-ai-engineers-red-hat-openshift-ai).

AutoRAG optimizes Retrieval-Augmented Generation pipelines -- it benchmarks configurations across document parsing, query expansion, retrieval strategy, passage reranking, and end-to-end evaluation to find the best-performing RAG pattern. AutoML automates machine learning model selection and hyperparameter tuning for tabular and time-series data. Both run on Kubeflow Pipelines.

This repository provides self-contained command-line utilities that interact with the infrastructure components these systems depend on: vector databases, object storage, pipeline runners, and Kubernetes secrets. Each tool is packaged under `autox_tools` and registered as a `uv`-runnable entry point for zero-friction use in development workflows.

## Prerequisites

- Python 3.11 -- 3.13
- [uv](https://docs.astral.sh/uv/) package manager

## Quick start

```bash
# Clone and install
git clone <repo-url> && cd autox-tools
uv sync

# Verify
uv run vs milvus --help
```

## Available tools

| Entry point | Package | Description |
|---|---|---|
| `pipelines` | [`autox_tools/pipelines/`](autox_tools/pipelines/README.md) | Submit, monitor, and inspect Kubeflow Pipeline runs -- run submission, status, live progress, pod logs, and S3 artifacts |
| `autorag` | [`autox_tools/autorag/`](autox_tools/autorag/README.md) | Analyze AutoRAG experiment results -- leaderboard ranking, side-by-side comparison, PDF reports, RAG pattern browsing, and artifact export |
| `automl` | [`autox_tools/automl/`](autox_tools/automl/README.md) | AutoML experiment management -- placeholder for future result analysis tooling |
| `s3` | [`autox_tools/s3/`](autox_tools/s3/README.md) | Browse, download, upload, and clean up S3/MinIO experiment artifacts |
| `vs milvus` | [`autox_tools/vs/milvus/`](autox_tools/vs/milvus/README.md) | Manage remote Milvus vector database instances -- list, inspect, query, export, and maintain collections |
| `vs pgvector` | [`autox_tools/vs/pgvector/`](autox_tools/vs/pgvector/README.md) | Manage PostgreSQL/pgvector tables -- list, inspect, query, export, and maintain vector tables |
| `ogx` | [`autox_tools/ogx/`](autox_tools/ogx/README.md) | Inspect and test models, providers, and vector stores on an OGX gateway |
| `maas` | [`autox_tools/maas/`](autox_tools/maas/README.md) | List and sanity-check models served by OpenShift MaaS (Model-as-a-Service) |
| `secrets` | [`autox_tools/secrets/`](autox_tools/secrets/README.md) | Manage Kubernetes Opaque secrets -- list, decode, create, update, and delete key-value secrets |
| `config` | [`autox_tools/config/`](autox_tools/config/README.md) | Manage configuration profiles -- list, show, validate, and initialize `.autox.yaml` |

## Typical workflow

Submit an AutoRAG or AutoML experiment, monitor it, and inspect results -- all from the terminal:

```bash
# 1. Submit a pipeline run from a JSON config
uv run pipelines -p dev run autorag-config.json --watch

# 2. Once complete, view evaluation metrics
uv run autorag -p dev results <run-id>

# 3. Compare against a previous run
uv run autorag -p dev compare <run-id-1> <run-id-2>

# 4. Download artifacts for offline analysis
uv run pipelines -p dev artifacts <run-id> --download ./results/
```

The `-p dev` flag selects the `dev` profile from `.autox.yaml`. If `defaults.profile` is set in the config, or the `AUTOX_PROFILE` env var is exported, the flag can be omitted entirely.

See the [pipelines README](autox_tools/pipelines/README.md) for example config files for AutoRAG and AutoML.

## Configuration

### Profile-based configuration (`.autox.yaml`)

The recommended approach is a `.autox.yaml` file in the project root. It defines named service configs and composable profiles so you can switch between environments (dev, staging, prod) with a single flag:

```bash
uv run s3 -p dev list                      # use the "dev" profile (bucket from config)
uv run s3 -t minio-dev list               # target a named S3 config directly
uv run pipelines -p staging status <id>    # multi-service profile
```

Generate a starter config from the bundled template:

```bash
uv run config init
```

See [`.autox.yaml.example`](.autox.yaml.example) for the full annotated reference. Secrets support `${ENV_VAR}` interpolation so raw credentials stay out of the file.

**Resolution order** (highest priority wins):

1. `--target / -t` -- named service config (single-service CLIs only)
2. `--profile / -p` -- profile name
3. `AUTOX_PROFILE` environment variable
4. `defaults.profile` in the config file
5. `.env` fallback (full backward compatibility)

Manage configs with `uv run config list`, `show`, and `validate`.

**Config file discovery** walks up from the current directory, then falls back to the `AUTOX_CONFIG` env var. Running [`install.sh`](install.sh) creates an `axt` alias that pins both `AUTOX_CONFIG` (the project's `.autox.yaml`) and `--env-file` (the project's `.env`), so profiles and env vars resolve identically from **any** directory:

```bash
./install.sh          # adds: alias axt="AUTOX_CONFIG=.../.autox.yaml uv run --project ... --env-file .../.env"
axt s3 -p dev list    # then run from anywhere
```

### Environment variables (`.env`)

Each tool also reads connection settings from environment variables. Place a `.env` file in the project root (or any parent directory) to avoid exporting variables manually. This path is used automatically when no `.autox.yaml` exists. See individual tool READMEs for required variables.

| Prefix | Purpose | Used by |
|---|---|---|
| `AWS_*` | Data storage (experiment assets, datasets) | `s3` tool |
| `ARTIFACTS_AWS_*` | Pipeline artifacts (evaluation results, notebooks, leaderboard) | `pipelines artifacts` subcommand |
| `OGX_CLIENT_*` | OGX gateway connection (base URL, API key) | `ogx` tool |
| `MAAS_*` | OpenShift MaaS connection (base URL, API key, TLS verification) | `maas` tool |
| `RHOAI_*`, `K8S_*` | OpenShift cluster auth and K8S API access | `pipelines`, `secrets` tools |

## Development

```bash
# Install with dev dependencies
uv sync --group dev

# Lint
uv run ruff check autox_tools/

# Type-check
uv run mypy autox_tools/

# Test
uv run pytest
```

### Project structure

```
autox-tools/
  autox_tools/
    __init__.py
    config/            # Profile-based configuration system
      __init__.py
      _models.py       #   Frozen dataclasses for service configs and profiles
      _loader.py       #   YAML parsing, env-var interpolation, profile resolution
      cli.py           #   Config management CLI (list, show, validate, init)
    autorag/           # AutoRAG experiment analysis tool
      __init__.py
      _artifacts.py    #   Artifact download and categorization
      _display.py      #   Table formatting and terminal output
      _patterns.py     #   RAG pattern discovery and parsing
      _report.py       #   PDF report generation (requires matplotlib)
      _resolver.py     #   S3 artifact path resolution from KFP run metadata
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    automl/            # AutoML experiment management tool (placeholder)
      __init__.py
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    vs/                # Unified vector store CLI (dispatcher + backends)
      __init__.py
      cli.py           #   Dispatcher: routes "vs milvus" / "vs pgvector"
      milvus/          #   Milvus backend
        __init__.py
        _client.py     #     Connection factory (config or env vars -> MilvusClient)
        cli.py         #     argparse entry point and subcommands
        README.md      #     Command reference and setup guide
      pgvector/        #   pgvector backend
        __init__.py
        _client.py     #     Connection factory (config or env vars -> psycopg.Connection)
        cli.py         #     argparse entry point and subcommands
        README.md      #     Command reference and setup guide
    ogx/               # OGX gateway CLI tool
      __init__.py
      _client.py       #   Connection factory (config or env vars -> OgxClient)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    maas/              # OpenShift MaaS CLI tool
      __init__.py
      _client.py       #   Settings resolution + OpenAI client / endpoint derivation
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    secrets/           # Kubernetes secret management tool
      __init__.py
      _client.py       #   K8S client factory (config or env vars -> CoreV1Api)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    pipelines/         # KFP pipeline management tool
      __init__.py
      _kfp.py          #   KFP client factory (config or env vars -> kfp.Client)
      _k8s.py          #   Kubernetes client factory with API URL derivation
      _artifacts_s3.py #   S3 client factory for pipeline artifacts
      _filters.py      #   Task noise filtering (hides scaffolding tasks)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    s3/                # S3/MinIO asset management tool
      __init__.py
      _client.py       #   S3 client factory (config or env vars -> boto3 client)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
  tests/
  .autox.yaml.example  # Annotated config template
  pyproject.toml
```

### Adding a new tool

1. Create a subpackage under `autox_tools/` (e.g. `autox_tools/s3/`).
2. Implement a `cli.py` with a `main()` function and a `_client.py` connection factory.
3. Register an entry point in `pyproject.toml` under `[project.scripts]`.
4. Add any new dependencies to the `dependencies` list.
5. Add a `README.md` with environment variable docs and command reference.

## Upstream ecosystem

This tooling supports and complements the following projects:

| Repository | Role |
|---|---|
| [ai4rag](https://github.com/IBM/ai4rag) | RAG optimization engine (Apache-2.0). Uses [OGX](https://github.com/IBM/ogx) for embeddings, vector stores, and LLM inference; GAMOptimizer for hyperparameter search across RAG patterns. |
| [pipelines-components](https://github.com/opendatahub-io/pipelines-components) | Reusable Kubeflow Pipeline components for training, evaluation, data processing, and deployment (OpenDataHub). |
| [autox-ci](https://github.com/red-hat-data-services/autox-ci) | End-to-end test suite for AutoRAG and AutoML on OpenShift AI. Contains shared utilities for KFP progress monitoring, S3 asset management, and failure diagnostics. |

AutoML uses [AutoGluon](https://auto.gluon.ai/) as its optimization backend and Kubeflow Pipelines as the execution runner.
