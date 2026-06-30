# `autorag` -- AutoRAG Experiment Management CLI

Fetch, display, compare, and export AutoRAG experiment results by KFP run ID. Browse and download RAG pattern artifacts from S3/MinIO storage.

## Setup

### Profile-based configuration (recommended)

Define named RHOAI and artifacts S3 configs in `.autox.yaml` and select them via profile:

```bash
uv run autorag -p dev results <run-id>
uv run autorag -p staging compare <run-id-1> <run-id-2>
```

Since `autorag` uses multiple services (KFP for run metadata, artifacts S3 for results), it accepts `--profile/-p` to resolve all services at once. The `--target/-t` flag is not available -- use profiles instead.

See the [main README](../../README.md#configuration) for full details on `.autox.yaml` profiles and resolution order.

### Environment variables

Alternatively, the tool reads KFP and artifacts S3 credentials from environment variables or a `.env` file in the project root.

#### Required variables

| Variable | Description |
|---|---|
| `RHOAI_KFP_URL` | KFP API endpoint URL (must end with `/`) |
| `RHOAI_TOKEN` | Bearer token for KFP authentication |
| `RHOAI_PROJECT_NAME` | OpenShift namespace where pipelines run |
| `ARTIFACTS_AWS_S3_ENDPOINT` | S3 endpoint for pipeline artifacts |
| `ARTIFACTS_AWS_ACCESS_KEY_ID` | Artifacts S3 access key |
| `ARTIFACTS_AWS_SECRET_ACCESS_KEY` | Artifacts S3 secret key |

#### Optional variables

| Variable | Default | Description |
|---|---|---|
| `ARTIFACTS_AWS_DEFAULT_REGION` | `us-east-1` | S3 region |
| `ARTIFACTS_S3_VERIFY_TLS` | `true` | Set `false` to skip TLS verification |
| `KFP_VERIFY_SSL` | `true` | Set `false` to skip KFP TLS verification |
| `ARTIFACTS_S3_BUCKET` | -- | Fallback bucket when run metadata lacks an `s3://` path |

## Commands

All commands accept a global `--json` flag for machine-readable output.

### `results` -- Display experiment metrics

```bash
uv run autorag results <run-id>
uv run autorag results <run-id> --prefix custom/path/
uv run autorag results <run-id> --detailed                # per-pattern breakdown
uv run autorag results <run-id> --detailed --top-n 3      # top 3 patterns
uv run autorag results <run-id> --sort-by answer_correctness
uv run autorag results <run-id> --pdf report.pdf          # generate PDF report
uv run autorag results <run-id> --names "abc123=Baseline"
uv run autorag --json results <run-id>
```

Resolves the S3 artifact location, downloads `evaluation_results.json`, and displays metrics in a leaderboard.

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
uv run autorag compare <run-id-1> <run-id-2>
uv run autorag compare <run-id-1> <run-id-2> --metrics answer_correctness,faithfulness
uv run autorag compare <run-id-1> <run-id-2> --detailed     # per-run leaderboards
uv run autorag compare <run-id-1> <run-id-2> --pdf diff.pdf # PDF report
uv run autorag compare <run-id-1> <run-id-2> --names "id1=Baseline,id2=New Config"
uv run autorag --json compare <run-id-1> <run-id-2>
```

Compares metrics from two runs side-by-side with delta values and direction indicators.

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
uv run autorag export <run-id>
uv run autorag export <run-id> -o ./my-experiment/
uv run autorag export <run-id> --prefix custom/path/ --bucket my-bucket
```

Downloads all artifacts for a run to a local directory, preserving the S3 directory structure. Shows a categorized summary table before downloading. Accepts `--prefix` and `--bucket` overrides for manual S3 resolution.

### `info` -- Run metadata and artifact summary

```bash
uv run autorag info <run-id>
uv run autorag info <run-id> --prefix custom/path/ --bucket my-bucket
uv run autorag --json info <run-id>
```

Displays KFP run metadata (state, timing, duration) alongside a listing of all artifacts and their sizes, without downloading any files.

### `artifacts` -- Browse and download RAG pattern artifacts

```bash
# Summary view (category counts + pattern names)
uv run autorag artifacts <run-id>
uv run autorag --json artifacts <run-id>

# Download all artifacts
uv run autorag artifacts <run-id> --download ./results/

# Browse RAG patterns
uv run autorag artifacts <run-id> --pattern all              # List all patterns
uv run autorag artifacts <run-id> --pattern Pattern1         # Files in Pattern1
uv run autorag artifacts <run-id> --pattern Pattern1 --download ./out/

# Print a single artifact to stdout (pipe-friendly)
uv run autorag artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json
uv run autorag artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json | jq .
```

| Flag | Description |
|---|---|
| `--prefix` | Explicit S3 prefix override (skip auto-resolution) |
| `--bucket` | Explicit bucket override |
| `--pattern` | RAG pattern name or `all` to list all patterns |
| `--artifact` | Artifact filename within a pattern (requires `--pattern`); prints content to stdout by default |
| `--download DIR` | Download artifacts to directory |

Pattern name matching is case-insensitive and supports substring matching (e.g. `--pattern optim` matches `OptimizedChunking`).

## Artifact resolution

The tool automatically resolves S3 artifact locations from KFP run metadata:

1. **Explicit override**: `--prefix` and `--bucket` flags bypass auto-resolution.
2. **Run parameters**: Extracts `pipeline_root` from KFP `runtime_config`.
3. **Convention scan**: Probes common prefix patterns (`artifacts/{run_id}/`, etc.).

The resolver also refines the prefix to include the run ID when the `pipeline_root` points at the pipeline level rather than the run level.

## Architecture

```
autox_tools/autorag/
    __init__.py        Package marker
    _artifacts.py      Artifact download and categorization
    _display.py        Table formatting and terminal output
    _patterns.py       RAG pattern discovery and parsing
    _report.py         PDF report generation (requires matplotlib)
    _resolver.py       S3 artifact path resolution from KFP run metadata
    cli.py             argparse entry point and subcommands
    README.md          This file
```

## Example workflows

### Analyze experiment results

```bash
# View the leaderboard for a completed AutoRAG run
uv run autorag results <run-id>

# Compare two experiments side-by-side
uv run autorag compare <run-id-1> <run-id-2> --detailed

# Generate a PDF report for stakeholders
uv run autorag results <run-id> --pdf evaluation-report.pdf
```

### Browse RAG patterns

```bash
# Discover what patterns were evaluated
uv run autorag artifacts <run-id>

# List all patterns with file counts
uv run autorag artifacts <run-id> --pattern all

# Print a single artifact to stdout
uv run autorag artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json

# Pipe evaluation JSON into jq for quick analysis
uv run autorag artifacts <run-id> --pattern Pattern1 --artifact evaluation_results.json | jq '.metrics'

# Download an entire pattern's artifacts
uv run autorag artifacts <run-id> --pattern Pattern1 --download ./pattern-results/
```

### Full experiment workflow

```bash
# 1. Submit an AutoRAG pipeline run
uv run pipelines run autorag-config.json --watch

# 2. View evaluation metrics
uv run autorag results <run-id>

# 3. Browse RAG patterns and export specific results
uv run autorag artifacts <run-id> --pattern all
uv run autorag artifacts <run-id> --pattern BestPattern --download ./best/

# 4. Compare against a baseline
uv run autorag compare <run-id> <baseline-id> --pdf comparison.pdf
```
