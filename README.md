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
uv run milvus --help
```

## Available tools

| Entry point | Package | Description |
|---|---|---|
| `pipelines` | [`autox_tools/pipelines/`](autox_tools/pipelines/README.md) | Submit, monitor, and inspect Kubeflow Pipeline runs -- run submission, status, live progress, pod logs, and S3 artifacts |
| `experiments` | [`autox_tools/experiments/`](autox_tools/experiments/README.md) | Analyze and compare experiment results -- metrics display, side-by-side comparison, and artifact export |
| `s3` | [`autox_tools/s3/`](autox_tools/s3/README.md) | Browse, download, upload, and clean up S3/MinIO experiment artifacts |
| `milvus` | [`autox_tools/milvus/`](autox_tools/milvus/README.md) | Manage remote Milvus vector database instances -- list, inspect, query, export, and maintain collections |
| `ogx` | [`autox_tools/ogx/`](autox_tools/ogx/README.md) | Inspect and test models, providers, and vector stores on an OGX gateway |
| `secrets` | [`autox_tools/secrets/`](autox_tools/secrets/README.md) | Manage Kubernetes Opaque secrets -- list, decode, create, update, and delete key-value secrets |

## Typical workflow

Submit an AutoRAG or AutoML experiment, monitor it, and inspect results -- all from the terminal:

```bash
# 1. Submit a pipeline run from a JSON config
uv run pipelines run autorag-config.json --watch

# 2. Once complete, view evaluation metrics
uv run experiments results <run-id>

# 3. Compare against a previous run
uv run experiments compare <run-id-1> <run-id-2>

# 4. Download artifacts for offline analysis
uv run pipelines artifacts <run-id> --download ./results/
```

See the [pipelines README](autox_tools/pipelines/README.md) for example config files for AutoRAG and AutoML.

## Configuration

Each tool reads its connection settings from environment variables. Place a `.env` file in the project root (or any parent directory) to avoid exporting variables manually. See individual tool READMEs for required variables.

Two independent S3 connections are supported:

| Prefix | Purpose | Used by |
|---|---|---|
| `AWS_*` | Data storage (experiment assets, datasets) | `s3` tool |
| `ARTIFACTS_AWS_*` | Pipeline artifacts (evaluation results, notebooks, leaderboard) | `pipelines artifacts` subcommand |
| `OGX_CLIENT_*` | OGX gateway connection (base URL, API key) | `ogx` tool |
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
    experiments/       # Experiment result analysis tool
      __init__.py
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    milvus/            # Milvus CLI tool
      __init__.py
      _client.py       #   Connection factory (env vars -> MilvusClient)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    ogx/               # OGX gateway CLI tool
      __init__.py
      _client.py       #   Connection factory (env vars -> OgxClient)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    secrets/           # Kubernetes secret management tool
      __init__.py
      _client.py       #   K8S client factory (env vars -> CoreV1Api)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    pipelines/         # KFP pipeline management tool
      __init__.py
      _kfp.py          #   KFP client factory (env vars -> kfp.Client)
      _k8s.py          #   Kubernetes client factory with API URL derivation
      _artifacts_s3.py #   S3 client factory for pipeline artifacts (ARTIFACTS_AWS_*)
      _filters.py      #   Task noise filtering (hides scaffolding tasks)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
    s3/                # S3/MinIO asset management tool
      __init__.py
      _client.py       #   S3 client factory (env vars -> boto3 client)
      cli.py           #   argparse entry point and subcommands
      README.md        #   Command reference and setup guide
  tests/
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
