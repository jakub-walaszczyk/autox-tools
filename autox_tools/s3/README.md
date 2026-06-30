# `s3` -- S3/MinIO Asset Management CLI

Browse, download, and clean up data assets stored in S3-compatible object
storage (AWS S3 or MinIO) used by AutoRAG and AutoML pipelines.

> **Note:** This tool connects to the **data-storage** S3 endpoint (`AWS_*`
> variables). Pipeline artifacts (evaluation results, notebooks, leaderboard
> reports) use a separate S3 connection configured via `ARTIFACTS_AWS_*`
> variables -- see the [`pipelines` tool README](../pipelines/README.md).

## Setup

### Option A: Profile-based configuration (recommended)

Define named S3 configs in `.autox.yaml` and select them via CLI flags.
Each S3 config can include an optional `bucket` field so you don't have to
pass it on every command:

```yaml
s3:
  minio-dev:
    endpoint: https://minio.dev.example.com
    access_key_id: ${AWS_ACCESS_KEY_ID}
    secret_access_key: ${AWS_SECRET_ACCESS_KEY}
    bucket: my-data-bucket          # optional default bucket
```

```bash
uv run s3 -p dev list                         # uses profile + config bucket
uv run s3 -t minio-dev list                   # target a named S3 config
uv run s3 -p dev list -b other-bucket         # override bucket from CLI
```

See the [main README](../../README.md#configuration) for full details on `.autox.yaml` profiles and resolution order. Run `uv run config init` to generate a starter config.

### Option B: Environment variables

Set the following environment variables (or add them to a `.env` file):

| Variable | Required | Default | Description |
|---|---|---|---|
| `AWS_S3_ENDPOINT` | yes | -- | S3 endpoint URL |
| `AWS_ACCESS_KEY_ID` | yes | -- | Access key |
| `AWS_SECRET_ACCESS_KEY` | yes | -- | Secret key |
| `AWS_DEFAULT_REGION` | no | `us-east-1` | Region name |
| `S3_VERIFY_TLS` | no | `true` | `false` to skip TLS verification |

This path is used automatically when no `.autox.yaml` is present or no profile/target is specified.

### Verify connectivity

```bash
uv run s3 list -b my-bucket
```

## Bucket resolution

The bucket is resolved in this order (highest priority wins):

1. `--bucket / -b` flag on the command line
2. `bucket` field in the resolved S3 config (`.autox.yaml`)
3. Error -- at least one of the above must be set

## Commands

All commands accept `--profile/-p` to select a profile, `--target/-t` to
select a named S3 config, and `--bucket/-b` to specify or override the bucket.

### `list` -- List objects

```bash
uv run s3 list                                        # config bucket, top-level
uv run s3 list experiments/ --recursive                # all objects under prefix
uv run s3 list -b my-bucket experiments/ --recursive   # explicit bucket
uv run s3 --json list experiments/                     # JSON output
uv run s3 -t minio-dev list                            # use a named S3 config
```

### `tree` -- Tree view

```bash
uv run s3 tree experiments/ --depth 2
uv run s3 tree -b my-bucket experiments/ --depth 2
```

### `download` -- Download objects

```bash
uv run s3 download experiments/run-abc/ -o ./local-results/
uv run s3 download -b my-bucket experiments/run-abc/ --pattern "*.json" -o ./results/
```

### `upload` -- Upload files

```bash
uv run s3 upload ./report.html results/
uv run s3 upload ./output-dir/ results/ -b my-bucket --recursive
```

### `cleanup` -- Delete artifacts

```bash
uv run s3 cleanup experiments/ --older-than 30 --dry-run    # preview
uv run s3 cleanup experiments/ --older-than 30 --yes        # execute
uv run s3 cleanup -b my-bucket experiments/ --pattern "*.tmp" --yes
```

## Architecture

```
autox_tools/s3/
    __init__.py    Package marker
    _client.py     S3 client factory (config or env vars -> boto3 client)
    cli.py         argparse entry point and subcommands
    README.md      This file
```

## Example Workflows

### Download experiment results after a pipeline run

```bash
uv run s3 tree experiments/run-20250520/ --depth 2
uv run s3 download experiments/run-20250520/ \
    --pattern "*.json" -o ./evaluation-results/
```

### Clean up stale artifacts older than 30 days

```bash
uv run s3 cleanup experiments/ --older-than 30 --dry-run
uv run s3 cleanup experiments/ --older-than 30 --yes
```
