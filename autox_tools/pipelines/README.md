# `pipelines` -- KFP Pipeline Management CLI

Monitor, inspect, debug, and launch Kubeflow Pipeline runs on OpenShift AI. Provides run submission from config files, status tracking, live progress monitoring, pod log retrieval, and S3 artifact browsing -- everything operators need without navigating the KFP dashboard.

## Setup

### Environment variables

Create a `.env` file in the project root or export the variables directly:

```bash
# Required -- KFP connection
RHOAI_KFP_URL=https://ds-pipeline-dspa.apps.cluster.example.com/
RHOAI_TOKEN=sha256~your-openshift-token
RHOAI_PROJECT_NAME=your-namespace

# Optional
KFP_VERIFY_SSL=true                       # Set to "false" for self-signed certs
```

For the `logs` command (Kubernetes pod access):

```bash
# Optional -- auto-derived from RHOAI_KFP_URL if unset
K8S_API_URL=https://api.cluster.example.com:6443
K8S_API_PORT=6443                          # Default: 6443 (OCP) / 443 (ROSA)
```

The K8S client reuses `KFP_VERIFY_SSL` for TLS verification, matching autox-ci's behavior.

For the `artifacts` command (pipeline artifacts S3 -- separate from the data-storage S3 used by the `s3` tool):

```bash
ARTIFACTS_AWS_S3_ENDPOINT=https://artifacts-minio.apps.cluster.example.com
ARTIFACTS_AWS_ACCESS_KEY_ID=your-artifacts-access-key
ARTIFACTS_AWS_SECRET_ACCESS_KEY=your-artifacts-secret-key
ARTIFACTS_S3_BUCKET=your-artifacts-bucket    # Used when KFP run config lacks a full s3:// URI
# Optional
ARTIFACTS_AWS_DEFAULT_REGION=us-east-1
ARTIFACTS_S3_VERIFY_TLS=true
```

### Verify connectivity

```bash
uv run pipelines list --limit 5
```

## Commands

All commands accept a global `--json` flag for machine-readable output.

### `run` -- Submit a pipeline run

Submit a pipeline run from a JSON config file that references a compiled KFP pipeline YAML and its parameters.

```bash
uv run pipelines run config.json                         # Submit and print run ID
uv run pipelines run config.json --watch                 # Submit and auto-monitor
uv run pipelines run config.json --dry-run               # Validate config without submitting
uv run pipelines run config.json --override optimization_metric=answer_correctness
uv run pipelines run config.json --run-name "my-run"     # Override run display name
uv run pipelines --json run config.json                  # JSON output
```

#### Config file format

| Field | Required | Description |
|---|---|---|
| `pipeline_package` | Yes | Path to compiled KFP pipeline YAML (absolute or relative to config file) |
| `experiment` | No | KFP experiment name for grouping (default: `Default`) |
| `run_name` | No | Display name for the run (auto-generated if omitted) |
| `parameters` | No | Dict of pipeline parameters passed to KFP |
| `service_account` | No | Kubernetes service account for the run |

The `--dry-run` flag skips KFP authentication entirely, making it useful for config validation in CI or before credentials are available.

#### AutoRAG example config

AutoRAG optimizes RAG pipelines via the `documents_rag_optimization_pipeline` from [ai4rag](https://github.com/IBM/ai4rag). It explores embedding and generation model combinations, evaluates RAG patterns against a test dataset, and selects the best configuration by the specified quality metric.

```json
{
  "pipeline_package": "pipelines/documents-rag-optimization.yaml",
  "experiment": "autorag-evaluation",
  "run_name": "faithfulness-eval-001",
  "parameters": {
    "test_data_secret_name": "test-data-s3-credentials",
    "test_data_bucket_name": "test-data",
    "test_data_key": "qa-pairs/golden-set.json",
    "input_data_secret_name": "input-docs-s3-credentials",
    "input_data_bucket_name": "knowledge-base",
    "input_data_key": "product-manuals/",
    "ogx_secret_name": "ogx-api-credentials",
    "vector_io_provider_id": "milvus-prod",
    "embedding_models": ["bge-large-en-v1.5", "all-minilm-l6-v2"],
    "generation_models": ["granite-3b-code-instruct", "granite-8b-code-instruct"],
    "optimization_metric": "faithfulness",
    "optimization_max_rag_patterns": 8
  },
  "service_account": "pipeline-runner-sa"
}
```

Parameter reference:

| Parameter | Required | Description |
|---|---|---|
| `test_data_secret_name` | Yes | K8S secret with S3 credentials for the test data bucket |
| `test_data_bucket_name` | Yes | S3 bucket containing the test data file |
| `test_data_key` | Yes | Object key of the test data JSON file |
| `input_data_secret_name` | Yes | K8S secret with S3 credentials for the input documents bucket |
| `input_data_bucket_name` | Yes | S3 bucket containing the input documents |
| `input_data_key` | No | Object key prefix for input documents (default: bucket root) |
| `ogx_secret_name` | Yes | K8S secret with `OGX_CLIENT_API_KEY` and `OGX_CLIENT_BASE_URL` |
| `vector_io_provider_id` | Yes | Vector I/O provider registered in OGX (e.g. Milvus instance) |
| `embedding_models` | No | List of embedding model IDs to include in the search space |
| `generation_models` | No | List of generation/LLM model IDs to include in the search space |
| `optimization_metric` | No | Quality metric: `faithfulness`, `answer_correctness`, or `context_correctness` (default: `faithfulness`) |
| `optimization_max_rag_patterns` | No | Maximum RAG patterns to generate (default: `8`) |

#### AutoML example config

AutoML automates model selection and hyperparameter tuning using [AutoGluon](https://auto.gluon.ai/) as its optimization backend. Parameters map to the compiled `automl-pipeline.yaml` from [pipelines-components](https://github.com/opendatahub-io/pipelines-components).

```json
{
  "pipeline_package": "pipelines/automl-pipeline.yaml",
  "experiment": "automl-tabular",
  "run_name": "sales-forecast-autogluon-001",
  "parameters": {
    "dataset_path": "s3://datasets/sales-data/train.csv",
    "target_column": "revenue",
    "task_type": "regression",
    "time_limit": "3600",
    "preset": "best_quality",
    "eval_metric": "root_mean_squared_error",
    "output": "s3://artifacts/automl-runs/sales-forecast-001/"
  },
  "service_account": "pipeline-runner-sa"
}
```

### `status` -- Get run status

Show run state and task-level progress for a given run ID.

```bash
uv run pipelines status <run-id>
uv run pipelines --json status <run-id>
```

KFP scaffolding tasks (drivers, loop iterators, root DAG nodes) are filtered automatically so only user-defined pipeline components are shown.

### `list` -- List recent runs

```bash
uv run pipelines list                              # Last 20 runs
uv run pipelines list --limit 50                   # More runs
uv run pipelines list --experiment my-experiment   # Filter by experiment
uv run pipelines list --state failed               # Filter by state
uv run pipelines --json list                       # JSON output
```

### `watch` -- Live progress monitoring

Poll a running pipeline and display task progress in real time. Updates in-place on TTY terminals, falls back to append-only on non-TTY.

```bash
uv run pipelines watch <run-id>                    # Default: 10s interval, 1h timeout
uv run pipelines watch <run-id> --interval 15      # Custom poll interval
uv run pipelines watch <run-id> --timeout 7200     # Custom timeout (2h)
```

Exit codes: `0` = succeeded, `1` = failed/error, `2` = timeout.

### `logs` -- Fetch pod logs

Retrieve container logs from pipeline task pods. By default shows only failed tasks; use `--all` to see all.

```bash
uv run pipelines logs <run-id>                     # Failed tasks only
uv run pipelines logs <run-id> --all               # All tasks
uv run pipelines logs <run-id> --task indexing      # Specific task
uv run pipelines logs <run-id> --tail 200           # More log lines
uv run pipelines --json logs <run-id>               # JSON output
```

Handles CrashLoopBackOff pods by also fetching previous container logs.

### `artifacts` -- Browse and download S3 artifacts

Browse output artifacts from a pipeline run stored in S3/MinIO. Default mode shows a category-based summary; use `--component` to drill into specific pipeline components.

```bash
# Summary view (category counts)
uv run pipelines artifacts <run-id>
uv run pipelines --json artifacts <run-id>

# Download all artifacts
uv run pipelines artifacts <run-id> --download ./results/

# Browse pipeline components
uv run pipelines artifacts <run-id> --component all                    # List all components
uv run pipelines artifacts <run-id> --component search-space-optimization  # Files in component
uv run pipelines artifacts <run-id> --component search-space-optimization --download ./out/
```

For AutoRAG-specific artifact browsing (RAG pattern discovery, per-pattern downloads, artifact content printing), use the [`autorag artifacts`](../autorag/README.md) command instead.

Requires artifacts S3 credentials (`ARTIFACTS_AWS_S3_ENDPOINT`, `ARTIFACTS_AWS_ACCESS_KEY_ID`, `ARTIFACTS_AWS_SECRET_ACCESS_KEY`). Without them, only artifact references are shown. These are separate from the `AWS_*` credentials used by the `s3` tool for data storage.

## Architecture

```
autox_tools/pipelines/
    __init__.py        Package marker
    _kfp.py            KFP client factory (env vars -> kfp.Client)
    _k8s.py            Kubernetes client factory with OCP/ROSA API URL derivation
    _artifacts_s3.py   S3 client factory for pipeline artifacts (ARTIFACTS_AWS_* env vars)
    _filters.py        Task noise filtering (hides KFP/Argo scaffolding)
    cli.py             argparse entry point and subcommands
    README.md          This file
```

## K8S API URL derivation

The `logs` command needs direct Kubernetes API access. The API URL is derived automatically from the KFP route URL:

| KFP route pattern | Derived API URL |
|---|---|
| `https://*.apps.<cluster>` | `https://api.<cluster>:6443` |
| `https://*.apps.rosa.<cluster>` | `https://api.<cluster>:443` |

Set `K8S_API_URL` to override if the convention does not apply.

## Example workflows

### Investigate a failed pipeline run

```bash
# Check overall status
uv run pipelines status abc-123-def-456

# Get logs from failed tasks
uv run pipelines logs abc-123-def-456

# Download artifacts for offline analysis
uv run pipelines artifacts abc-123-def-456 --download ./failed-run/
```

### Submit and monitor an AutoRAG experiment

```bash
# Validate the config first
uv run pipelines run autorag-config.json --dry-run

# Submit with a different optimization target and watch progress
uv run pipelines run autorag-config.json --override optimization_metric=answer_correctness --watch

# Or submit, then inspect results later
uv run pipelines run autorag-config.json
uv run pipelines status <run-id>
uv run autorag results <run-id>
```

### Submit an AutoML training run

```bash
# Dry-run to verify parameters
uv run pipelines run automl-config.json --dry-run

# Submit with a shorter time limit for quick iteration
uv run pipelines run automl-config.json --override time_limit=600 --watch
```

### Monitor a running pipeline

```bash
uv run pipelines watch abc-123-def-456 --interval 15
```
