# `config` -- Configuration Management CLI

Manage `.autox.yaml` configuration profiles -- list available profiles and service configs, inspect resolved values, validate references, and generate a starter config file.

## Commands

### `list` -- List profiles and service configs

```bash
uv run config list
```

Displays all defined profiles (with their service mappings) and all named service configurations grouped by type (s3, rhoai, vs.milvus, vs.pgvector, ogx, maas).

### `show` -- Show resolved config for a profile

```bash
uv run config show dev
uv run config show staging
```

Resolves a profile and displays the effective configuration for each mapped service. Secrets are masked in the output.

### `validate` -- Check config file for errors

```bash
uv run config validate
```

Parses `.autox.yaml` and checks for dangling references (profiles pointing to undefined service configs, undefined default profile). Exits with code 1 on validation failure.

### `init` -- Generate a starter config

```bash
uv run config init
uv run config init --force    # overwrite existing file
```

Copies the bundled `.autox.yaml.example` template to `.autox.yaml` in the current directory. Falls back to a minimal built-in template if the example file is missing.

## Configuration file format

The `.autox.yaml` file has four top-level sections:

| Section | Description |
|---|---|
| `defaults` | Default profile name (`defaults.profile`) |
| `profiles` | Named profiles mapping service types to named configs |
| `s3`, `rhoai`, `ogx`, `maas` | Top-level service config blocks |
| `vs.milvus`, `vs.pgvector` | Vector store configs nested under `vs` |

Values support `${ENV_VAR}` interpolation so raw credentials stay out of the file.

See [`.autox.yaml.example`](../../.autox.example.yaml) for a fully annotated reference and the [main README](../../README.md#configuration) for the resolution order.

## Architecture

```
autox_tools/config/
  __init__.py    Package marker
  _models.py     Frozen dataclasses for service configs and profiles
  _loader.py     YAML parsing, env-var interpolation, profile resolution
  cli.py         argparse entry point and subcommands
  README.md      This file
```
