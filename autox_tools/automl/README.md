# `automl` -- AutoML Experiment Management CLI

Placeholder CLI for managing AutoML experiment results on OpenShift AI. Additional subcommands will be added as the AutoML evaluation pipeline matures.

AutoML automates model selection and hyperparameter tuning using [AutoGluon](https://auto.gluon.ai/) as its optimization backend. Pipeline submission and monitoring are handled by the [`pipelines`](../pipelines/README.md) tool; this CLI will provide experiment-level analysis once the result format stabilizes.

## Setup

No additional configuration is required beyond what the `pipelines` tool uses. When result analysis subcommands are added, they will share the same `RHOAI_*` and `ARTIFACTS_AWS_*` credentials described in the [pipelines README](../pipelines/README.md).

## Commands

### `info` -- Show AutoML tooling status

```bash
uv run automl info
uv run automl --json info
```

Displays the current state of AutoML CLI tooling and lists planned subcommands.

## Architecture

```
autox_tools/automl/
    __init__.py    Package marker
    cli.py         argparse entry point and subcommands
    README.md      This file
```

## Planned subcommands

The following subcommands are planned as the AutoML pipeline stabilizes:

| Subcommand | Description |
|---|---|
| `results` | Display model evaluation metrics and rankings |
| `compare` | Side-by-side comparison of two AutoML runs |
| `artifacts` | Browse and download training artifacts from S3 |
| `export` | Download all artifacts for offline analysis |

## Current workflow

AutoML experiment submission and monitoring use the `pipelines` tool:

```bash
# Submit an AutoML training run
uv run pipelines run automl-config.json --watch

# Check run status
uv run pipelines status <run-id>

# Download artifacts
uv run pipelines artifacts <run-id> --download ./results/
```

See the [pipelines README](../pipelines/README.md) for AutoML config file format and parameter reference.
