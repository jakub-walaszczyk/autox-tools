# `pipelines` -- KFP Pipeline Management CLI

Monitor, inspect, and debug Kubeflow Pipeline runs on OpenShift AI. Provides run status tracking, live progress monitoring, pod log retrieval, and S3 artifact browsing -- everything operators need without navigating the KFP dashboard.

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

Browse output artifacts from a pipeline run stored in S3/MinIO. Default mode shows a category summary with RAG pattern discovery; use `--pattern` to drill into specific patterns.

```bash
# Summary view (category counts + pattern names)
uv run pipelines artifacts <run-id>
uv run pipelines --json artifacts <run-id>

# Download all artifacts
uv run pipelines artifacts <run-id> --download ./results/

# Browse pipeline components
uv run pipelines artifacts <run-id> --component all                    # List all components
uv run pipelines artifacts <run-id> --component search-space-optimization  # Files in component
uv run pipelines artifacts <run-id> --component search-space-optimization --download ./out/

# Browse RAG patterns
uv run pipelines artifacts <run-id> --pattern all              # List all patterns
uv run pipelines artifacts <run-id> --pattern Pattern1         # Files in Pattern1
uv run pipelines artifacts <run-id> --pattern Pattern1 --download ./out/  # Download pattern

# Single artifact from a pattern
uv run pipelines artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json

# Print artifact content to stdout (pipe-friendly)
uv run pipelines artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json --print
uv run pipelines artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json --print | jq .
```

Pattern name matching is case-insensitive and supports substring matching (e.g. `--pattern optim` matches `OptimizedChunking`). When `--artifact` is used without `--download`, the file is saved to the current directory.

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

### Browse RAG pattern results

```bash
# See artifact summary and discover pattern names
uv run pipelines artifacts abc-123-def-456

# List all patterns with file counts
uv run pipelines artifacts abc-123-def-456 --pattern all

# Download a single evaluation result
uv run pipelines artifacts abc-123-def-456 --pattern Pattern1 --artifact evaluation_results.json

# Download an entire pattern's artifacts
uv run pipelines artifacts abc-123-def-456 --pattern Pattern1 --download ./pattern-results/
```

### Monitor a running pipeline

```bash
uv run pipelines watch abc-123-def-456 --interval 15
```
