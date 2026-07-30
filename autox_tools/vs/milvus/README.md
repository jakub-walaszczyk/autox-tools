# Milvus CLI

Command-line tool for managing remote Milvus vector database instances. Covers collection inspection, data querying, bulk export, and operational maintenance -- everything a developer needs without writing ad-hoc Python scripts.

## Setup

### Profile-based configuration (recommended)

Define named Milvus configs under the `vs.milvus` section in `.autox.yaml`:

```yaml
vs:
  milvus:
    local-milvus:
      host: milvus.apps.dev-cluster.example.com
      port: 19530
      secure: false
      # server_pem_path: local/milvus.crt   # server/CA PEM for one-way TLS
```

For a TLS-enabled server, set `secure: true` and point `server_pem_path` at
the server/CA certificate. Relative paths resolve against the `.autox.yaml`
directory, so they work regardless of where you run the command.

Then select them via CLI flags:

```bash
uv run vs milvus -p dev health                   # use the "dev" profile
uv run vs milvus -t local-milvus list             # target a named config directly
```

See the [main README](../../../README.md#configuration) for full details on `.autox.yaml` profiles and resolution order. Run `uv run config init` to generate a starter config.

### Environment variables

Alternatively, create a `.env` file in the project root or export the variables directly:

```bash
# Required
MILVUS_HOST=localhost        # Hostname or IP of the Milvus server
MILVUS_PORT=19530            # gRPC port

# Optional
MILVUS_USER=root             # Authentication username
MILVUS_PASSWORD=Milvus       # Authentication password
MILVUS_SECURE=false          # Set to "true" to enable TLS
MILVUS_SERVER_PEM_PATH=      # Server/CA PEM cert for one-way TLS
```

This path is used automatically when no `.autox.yaml` is present or no profile/target is specified.

### Verify connectivity

```bash
uv run vs milvus health
```

## Commands

All commands accept a global `--json` flag for machine-readable output (useful for piping into `jq` or scripting). Use `--profile/-p` or `--target/-t` to select a config from `.autox.yaml`.

### Collection inspection

#### `list` -- List all collections

```bash
uv run vs milvus list              # Names only (fast)
uv run vs milvus list --counts     # Include row counts per collection
uv run vs milvus --json list       # JSON output
```

#### `describe` -- Show full collection details

Prints schema (fields, types, primary key), indexes, partitions, and row count.

```bash
uv run vs milvus describe my_collection
```

#### `count` -- Row count dashboard

Tabular view of row counts across all or filtered collections.

```bash
uv run vs milvus count                # All collections
uv run vs milvus count "embed_.*"     # Only collections matching a regex
```

#### `partitions` -- List partitions

```bash
uv run vs milvus partitions my_collection
```

### Data operations

#### `query` -- Run a filter expression

Execute a Milvus [filter expression](https://milvus.io/docs/boolean.md) and inspect rows without writing Python. Vector fields are summarized as `[N dims]` in human-readable output.

```bash
uv run vs milvus query my_collection 'id > 100'
uv run vs milvus query my_collection 'status == "active"' --output-fields name,score --limit 50
uv run vs milvus --json query my_collection 'id > 0' --limit 5
```

#### `export` -- Export data to JSONL

Dump collection data to a JSONL file for backup, offline inspection, or migration. Fetches in batches of 1000 internally.

```bash
uv run vs milvus export my_collection                          # All rows (up to 10k) -> my_collection.jsonl
uv run vs milvus export my_collection --filter 'score > 0.8'   # Filtered subset
uv run vs milvus export my_collection --limit 500 -o out.jsonl  # Custom limit and path
```

### Collection management

#### `drop` -- Drop collections by pattern

Matches collection names by prefix or full regex. Requires interactive confirmation unless `--yes` is passed.

```bash
uv run vs milvus drop "test_.*"              # Preview + confirm
uv run vs milvus drop "test_.*" --dry-run    # Preview only, no action
uv run vs milvus drop "test_.*" --yes        # Skip confirmation (CI use)
```

#### `rename` -- Rename a collection

```bash
uv run vs milvus rename old_name new_name
```

### Operational maintenance

#### `flush` -- Persist pending writes

Forces pending inserts/upserts to be written to storage. Useful before taking a backup or running an export.

```bash
uv run vs milvus flush my_collection
```

#### `compact` -- Trigger compaction

Reclaims storage space after bulk deletes by rewriting segment files.

```bash
uv run vs milvus compact my_collection
```

#### `health` -- Connection check and summary

Quick connectivity test that also reports total collections and row count.

```bash
uv run vs milvus health
```

## JSON output

Every command supports `--json` as a global flag (placed before the subcommand):

```bash
uv run vs milvus --json list --counts
uv run vs milvus --json describe my_collection
uv run vs milvus --json count "embed_.*"
uv run vs milvus --json health
```

This makes it straightforward to pipe into `jq`, feed into monitoring scripts, or integrate with other tooling:

```bash
# Get names of collections with more than 1M rows
uv run vs milvus --json count | jq -r '.collections[] | select(.row_count > 1000000) | .name'
```

## Architecture

```
autox_tools/vs/milvus/
  __init__.py    # Package marker
  _client.py     # Connection factory (config or env vars -> MilvusClient)
  cli.py         # argparse CLI with all subcommands
```

The tool is accessible via the unified vector store entry point in `pyproject.toml`:

```toml
[project.scripts]
vs = "autox_tools.vs.cli:main"
```
