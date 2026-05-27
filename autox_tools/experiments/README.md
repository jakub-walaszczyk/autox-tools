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
uv run experiments results <run-id> --detailed                # per-pattern breakdown
uv run experiments results <run-id> --detailed --top-n 3      # top 3 patterns
uv run experiments results <run-id> --sort-by answer_correctness
uv run experiments results <run-id> --pdf report.pdf          # generate PDF report
uv run experiments results <run-id> --names "abc123=Baseline"
uv run experiments --json results <run-id>
```

Resolves the S3 artifact location, downloads `evaluation_results.json`
(AutoRAG) or `metrics.json` (AutoML), and displays metrics in a leaderboard.

| Flag | Description |
|---|---|
| `--prefix` | Explicit S3 prefix override (skip auto-resolution) |
| `--bucket` | Explicit bucket override |
| `--detailed`, `-d` | Show pattern settings and per-pattern detail |
| `--top-n N` | Number of top patterns to show in detailed mode (default: 1) |
| `--sort-by METRIC` | Metric to rank the leaderboard by |
| `--pdf PATH` | Generate a PDF report at the given path |
| `--names` | Display name mapping (e.g. `"run-id=My Experiment"`) |

### `compare` -- Side-by-side metric comparison

```bash
uv run experiments compare <run-id-1> <run-id-2>
uv run experiments compare <run-id-1> <run-id-2> --metrics answer_correctness,faithfulness
uv run experiments compare <run-id-1> <run-id-2> --detailed     # per-run leaderboards
uv run experiments compare <run-id-1> <run-id-2> --pdf diff.pdf # PDF report
uv run experiments compare <run-id-1> <run-id-2> --names "id1=Baseline,id2=New Config"
uv run experiments --json compare <run-id-1> <run-id-2>
```

Compares metrics from two runs side-by-side with delta values and direction
indicators (`▲` = improvement, `▼` = regression).

| Flag | Description |
|---|---|
| `--metrics` | Comma-separated metric names to compare (default: all) |
| `--prefix1`, `--prefix2` | Explicit S3 prefix for each run |
| `--bucket` | Explicit bucket override (shared for both runs) |
| `--detailed`, `-d` | Show per-run leaderboards and pattern settings |
| `--pdf PATH` | Generate a PDF report at the given path |
| `--names` | Display name mapping (e.g. `"id1=Baseline,id2=New Config"`) |

### `export` -- Download all artifacts

```bash
uv run experiments export <run-id>
uv run experiments export <run-id> -o ./my-experiment/
uv run experiments export <run-id> --prefix custom/path/ --bucket my-bucket
```

Downloads all artifacts for a run to a local directory, preserving the S3
directory structure.  Shows a categorized summary table before downloading.
Accepts `--prefix` and `--bucket` overrides for manual S3 resolution.

### `info` -- Run metadata and artifact summary

```bash
uv run experiments info <run-id>
uv run experiments info <run-id> --prefix custom/path/ --bucket my-bucket
uv run experiments --json info <run-id>
```

Displays KFP run metadata (state, timing, duration) alongside a listing of
all artifacts and their sizes, without downloading any files.
Accepts `--prefix` and `--bucket` overrides for manual S3 resolution.

## Architecture

```
autox_tools/experiments/
    __init__.py        Package marker
    _artifacts.py      Artifact download and categorization
    _display.py        Table formatting and terminal output
    _patterns.py       RAG pattern discovery and parsing
    _report.py         PDF report generation (requires matplotlib)
    _resolver.py       S3 artifact path resolution from KFP run metadata
    cli.py             argparse entry point and subcommands
    README.md          This file
```

## Artifact resolution

The tool automatically resolves S3 artifact locations from KFP run metadata:

1. **Explicit override**: `--prefix` and `--bucket` flags bypass auto-resolution.
2. **Run parameters**: Extracts `pipeline_root` from KFP `runtime_config`.
3. **Convention scan**: Probes common prefix patterns (`artifacts/{run_id}/`, etc.).

The resolver also refines the prefix to include the run ID when the
`pipeline_root` points at the pipeline level rather than the run level.
