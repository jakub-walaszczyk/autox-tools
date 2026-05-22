# experiments -- Experiment Result Management CLI

Fetch, display, compare, and export AutoRAG/AutoML experiment results by KFP run ID.

## Setup

The tool requires both KFP and artifacts S3 credentials.  Set them via
environment variables or a `.env` file in the project root.

### Required variables

| Variable | Description |
|---|---|
| `RHOAI_KFP_URL` | KFP API endpoint URL (must end with `/`) |
| `RHOAI_TOKEN` | Bearer token for KFP authentication |
| `RHOAI_PROJECT_NAME` | OpenShift namespace where pipelines run |
| `ARTIFACTS_AWS_S3_ENDPOINT` | S3 endpoint for pipeline artifacts |
| `ARTIFACTS_AWS_ACCESS_KEY_ID` | Artifacts S3 access key |
| `ARTIFACTS_AWS_SECRET_ACCESS_KEY` | Artifacts S3 secret key |

### Optional variables

| Variable | Default | Description |
|---|---|---|
| `ARTIFACTS_AWS_DEFAULT_REGION` | `us-east-1` | S3 region |
| `ARTIFACTS_S3_VERIFY_TLS` | `true` | Set `false` to skip TLS verification |
| `KFP_VERIFY_SSL` | `true` | Set `false` to skip KFP TLS verification |
| `ARTIFACTS_S3_BUCKET` | — | Fallback bucket when run metadata lacks an `s3://` path |

## Commands

### `results` -- Display experiment metrics

```bash
uv run experiments results <run-id>
uv run experiments results <run-id> --prefix custom/path/
uv run experiments --json results <run-id>
```

Resolves the S3 artifact location, downloads `evaluation_results.json`
(AutoRAG) or `metrics.json` (AutoML), and displays metrics in a table.

### `compare` -- Side-by-side metric comparison

```bash
uv run experiments compare <run-id-1> <run-id-2>
uv run experiments compare <run-id-1> <run-id-2> --metrics answer_correctness,faithfulness
uv run experiments --json compare <run-id-1> <run-id-2>
```

Compares metrics from two runs side-by-side with delta values and direction
indicators (`▲` = improvement, `▼` = regression).

### `export` -- Download all artifacts

```bash
uv run experiments export <run-id>
uv run experiments export <run-id> -o ./my-experiment/
```

Downloads all artifacts for a run to a local directory, preserving the S3
directory structure.  Shows a categorized summary table before downloading.

### `info` -- Run metadata and artifact summary

```bash
uv run experiments info <run-id>
uv run experiments --json info <run-id>
```

Displays KFP run metadata (state, timing, duration) alongside a listing of
all artifacts and their sizes, without downloading any files.

## Artifact resolution

The tool automatically resolves S3 artifact locations from KFP run metadata:

1. **Explicit override**: `--prefix` and `--bucket` flags bypass auto-resolution.
2. **Run parameters**: Extracts `pipeline_root` from KFP `runtime_config`.
3. **Convention scan**: Probes common prefix patterns (`artifacts/{run_id}/`, etc.).

The resolver also refines the prefix to include the run ID when the
`pipeline_root` points at the pipeline level rather than the run level.
