# pgvector CLI

Command-line tool for managing PostgreSQL databases with the pgvector extension. Covers table inspection, data querying, bulk export, and operational maintenance -- everything a developer needs without writing ad-hoc SQL scripts.

## Setup

### Profile-based configuration (recommended)

Define named pgvector configs under the `vs.pgvector` section in `.autox.yaml`:

```yaml
vs:
  pgvector:
    local-pgvector:
      host: pgvector.apps.dev-cluster.example.com
      port: 5432
      database: vectordb
      sslmode: prefer
```

Then select them via CLI flags:

```bash
uv run vs pgvector -p dev health                    # use the "dev" profile
uv run vs pgvector -t local-pgvector list            # target a named config directly
```

See the [main README](../../../README.md#configuration) for full details on `.autox.yaml` profiles and resolution order. Run `uv run config init` to generate a starter config.

### Environment variables

Alternatively, create a `.env` file in the project root or export the variables directly:

```bash
# Required
PGVECTOR_HOST=localhost        # Hostname or IP of the PostgreSQL server
PGVECTOR_PORT=5432             # PostgreSQL port
PGVECTOR_DATABASE=vectordb     # Database name

# Optional
PGVECTOR_USER=postgres         # Authentication username
PGVECTOR_PASSWORD=secret       # Authentication password
PGVECTOR_SSLMODE=prefer        # SSL mode (default: "prefer")
```

This path is used automatically when no `.autox.yaml` is present or no profile/target is specified.

### Verify connectivity

```bash
uv run vs pgvector health
```

## Commands

All commands accept a global `--json` flag for machine-readable output (useful for piping into `jq` or scripting). Use `--profile/-p` or `--target/-t` to select a config from `.autox.yaml`.

### Table inspection

#### `list` -- List tables with vector columns

```bash
uv run vs pgvector list              # Names only (fast)
uv run vs pgvector list --counts     # Include row counts per table
uv run vs pgvector --json list       # JSON output
```

#### `describe` -- Show full table details

Prints column schema (names, types, nullability), indexes, and row count.

```bash
uv run vs pgvector describe my_table
```

#### `count` -- Row count dashboard

Tabular view of row counts across all or filtered tables.

```bash
uv run vs pgvector count                # All vector tables
uv run vs pgvector count "embed_.*"     # Only tables matching a regex
```

#### `indexes` -- List indexes

Show all indexes on a table with their definitions and sizes.

```bash
uv run vs pgvector indexes my_table
```

### Data operations

#### `query` -- Run a WHERE clause

Execute a SQL WHERE clause and inspect rows without writing ad-hoc scripts.

```bash
uv run vs pgvector query my_table 'id > 100'
uv run vs pgvector query my_table 'status = '"'"'active'"'"'' --output-fields name,score --limit 50
uv run vs pgvector --json query my_table 'id > 0' --limit 5
```

#### `export` -- Export data to JSONL

Dump table data to a JSONL file for backup, offline inspection, or migration.

```bash
uv run vs pgvector export my_table                          # All rows (up to 10k) -> my_table.jsonl
uv run vs pgvector export my_table --filter 'score > 0.8'   # Filtered subset
uv run vs pgvector export my_table --limit 500 -o out.jsonl  # Custom limit and path
```

### Table management

#### `drop` -- Drop tables by pattern

Matches table names by prefix or full regex. Only targets tables with vector columns. Requires interactive confirmation unless `--yes` is passed.

```bash
uv run vs pgvector drop "test_.*"              # Preview + confirm
uv run vs pgvector drop "test_.*" --dry-run    # Preview only, no action
uv run vs pgvector drop "test_.*" --yes        # Skip confirmation (CI use)
```

#### `rename` -- Rename a table

```bash
uv run vs pgvector rename old_name new_name
```

### Operational maintenance

#### `vacuum` -- Run VACUUM

Reclaims storage space and updates statistics. The PostgreSQL analog of Milvus compact/flush.

```bash
uv run vs pgvector vacuum my_table
```

#### `health` -- Connection check and summary

Quick connectivity test that reports PostgreSQL version, pgvector extension version, total vector tables, and row count.

```bash
uv run vs pgvector health
```

## JSON output

Every command supports `--json` as a global flag (placed before the subcommand):

```bash
uv run vs pgvector --json list --counts
uv run vs pgvector --json describe my_table
uv run vs pgvector --json count "embed_.*"
uv run vs pgvector --json health
```

This makes it straightforward to pipe into `jq`, feed into monitoring scripts, or integrate with other tooling:

```bash
# Get names of tables with more than 1M rows
uv run vs pgvector --json count | jq -r '.tables[] | select(.row_count > 1000000) | .name'
```

## Architecture

```
autox_tools/vs/pgvector/
  __init__.py    # Package marker
  _client.py     # Connection factory (config or env vars -> psycopg.Connection)
  cli.py         # argparse CLI with all subcommands
```

The tool is accessible via the unified vector store entry point in `pyproject.toml`:

```toml
[project.scripts]
vs = "autox_tools.vs.cli:main"
```
