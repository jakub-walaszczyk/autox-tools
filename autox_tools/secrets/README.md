# secrets

Manage Kubernetes Opaque secrets on OpenShift AI clusters. List, decode, create, update, and delete key-value secrets used by AutoRAG and AutoML workloads.

## Setup

The secrets tool reuses the same authentication and cluster variables as the `pipelines` tool. No additional environment variables are required.

| Variable | Required | Description |
|---|---|---|
| `RHOAI_TOKEN` | Yes | OpenShift bearer token (`sha256~...`) |
| `K8S_API_URL` | No | K8S API URL (e.g. `https://api.cluster:6443`). Preferred over derivation. |
| `RHOAI_KFP_URL` | No | KFP route URL. Used to derive K8S API URL when `K8S_API_URL` is not set. |
| `KFP_VERIFY_SSL` | No | TLS verification (`true`/`false`, default: `true`) |
| `RHOAI_PROJECT_NAME` | No | Default namespace (overridable with `--namespace`) |

The API URL is resolved in order: `K8S_API_URL` > derived from `RHOAI_KFP_URL` > error.

### Verify connectivity

```bash
uv run secrets list
```

## Commands

### list

List Opaque secrets in a namespace.

```bash
uv run secrets list
uv run secrets list -n other-namespace
uv run secrets list --filter minio
uv run secrets list --labels app=autorag,env=prod
uv run secrets list --json
```

### reveal

Decode and display a secret's key-value data. Values are base64-decoded automatically.

```bash
uv run secrets reveal my-db-creds
uv run secrets reveal my-db-creds -n prod-namespace
uv run secrets reveal my-db-creds --json
```

### create

Create a new Opaque secret from literal values and/or a dotenv-format file.

```bash
# From literals
uv run secrets create db-creds \
  --from-literal DB_HOST=pg.svc.cluster.local \
  --from-literal DB_USER=admin \
  --from-literal DB_PASS=s3cret

# From an env file
uv run secrets create db-creds --from-env-file ./credentials.env

# Combined (literals override file values on key collision)
uv run secrets create db-creds \
  --from-env-file ./base.env \
  --from-literal DB_PASS=override-password

# With labels
uv run secrets create db-creds \
  --from-literal DB_HOST=pg.svc \
  --labels app=autorag,env=prod

# Skip confirmation
uv run secrets create db-creds --from-literal key=value -y
```

### delete

Delete a secret from the namespace. Refuses to delete non-Opaque secrets.

```bash
uv run secrets delete db-creds
uv run secrets delete db-creds -n other-namespace
uv run secrets delete db-creds -y          # skip confirmation
uv run secrets delete db-creds --json
```

### edit

Update an existing Opaque secret. Add, update, or remove individual keys.

```bash
# Add or update keys
uv run secrets edit db-creds --set DB_PASS=new-password

# Remove a key
uv run secrets edit db-creds --remove OLD_KEY

# Combined
uv run secrets edit db-creds \
  --set NEW_KEY=value \
  --remove DEPRECATED_KEY

# Skip confirmation
uv run secrets edit db-creds --set key=value -y
```

## Global flags

| Flag | Description |
|---|---|
| `--json` | Machine-readable JSON output |
| `-n`, `--namespace` | Target namespace (default: `RHOAI_PROJECT_NAME`) |

## Architecture

```
autox_tools/secrets/
  __init__.py       # Package marker
  _client.py        # K8S CoreV1Api factory (env vars -> authenticated client)
  cli.py            # argparse entry point and subcommands
  README.md         # This file
```
