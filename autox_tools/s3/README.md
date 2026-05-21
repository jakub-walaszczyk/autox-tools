# `s3` -- S3/MinIO Asset Management CLI

Browse, download, and clean up data assets stored in S3-compatible object
storage (AWS S3 or MinIO) used by AutoRAG and AutoML pipelines.

> **Note:** This tool connects to the **data-storage** S3 endpoint (`AWS_*`
> variables). Pipeline artifacts (evaluation results, notebooks, leaderboard
> reports) use a separate S3 connection configured via `ARTIFACTS_AWS_*`
> variables -- see the [`pipelines` tool README](../pipelines/README.md).

## Setup

Set the following environment variables (or add them to a `.env` file):

| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_S3_ENDPOINT` | yes | -- | S3 endpoint URL |
| `AWS_ACCESS_KEY_ID` | yes | -- | Access key |
| `AWS_SECRET_ACCESS_KEY` | yes | -- | Secret key |
| `AWS_DEFAULT_REGION` | no | `us-east-1` | Region name |
| `S3_VERIFY_TLS` | no | `true` | `false` to skip TLS verification |

Verify connectivity:

```bash
uv run s3 list <bucket>
```

## Commands

### `list` -- List objects

```bash
uv run s3 list my-bucket                          # top-level listing
uv run s3 list my-bucket experiments/ --recursive  # all objects under prefix
uv run s3 --json list my-bucket experiments/       # JSON output
```

### `tree` -- Tree view

```bash
uv run s3 tree my-bucket experiments/ --depth 2
```

### `download` -- Download objects

```bash
uv run s3 download my-bucket experiments/run-abc/ -o ./local-results/
uv run s3 download my-bucket experiments/run-abc/ --pattern "*.json" -o ./results/
```

### `upload` -- Upload files

```bash
uv run s3 upload ./report.html my-bucket results/
uv run s3 upload ./output-dir/ my-bucket results/ --recursive
```

### `cleanup` -- Delete artifacts

```bash
uv run s3 cleanup my-bucket experiments/ --older-than 30 --dry-run   # preview
uv run s3 cleanup my-bucket experiments/ --older-than 30 --yes       # execute
uv run s3 cleanup my-bucket experiments/ --pattern "*.tmp" --yes     # by pattern
```

## Architecture

```
autox_tools/s3/
    __init__.py    Package marker
    _client.py     S3 client factory (env vars -> boto3 client)
    cli.py         argparse entry point and subcommands
    README.md      This file
```

## Example Workflows

### Download experiment results after a pipeline run

```bash
uv run s3 tree my-bucket experiments/run-20250520/ --depth 2
uv run s3 download my-bucket experiments/run-20250520/ \
    --pattern "*.json" -o ./evaluation-results/
```

### Clean up stale artifacts older than 30 days

```bash
uv run s3 cleanup my-bucket experiments/ --older-than 30 --dry-run
uv run s3 cleanup my-bucket experiments/ --older-than 30 --yes
```
