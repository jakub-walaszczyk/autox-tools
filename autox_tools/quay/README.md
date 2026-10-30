# `quay` — tagged images and repository discovery

Read-only Quay API tooling; no Docker, Podman, image layers, or GitHub access needed.
All commands output JSON by default.

```bash
# Full details for the image currently assigned to a PR tag
uv run quay latest opendatahub/odh-autorag:odh-pr-363

# Three most recent assignments of the rolling tag, including previous digests
uv run quay latest opendatahub/odh-autorag:odh-pr -n 3

# A digest-pinned reference for deployment
uv run quay latest opendatahub/odh-autorag:odh-pr-363 --output digest

# Resolve a known digest directly
uv run quay latest opendatahub/odh-autorag@sha256:<64-hex-characters>

# Use a configured registry and namespace
axt quay -t odh latest odh-autorag:odh-pr-363

# Optional repository and tag discovery
uv run quay search odh-dashboard
uv run quay search autorag --org opendatahub
uv run quay tags opendatahub/odh-autorag --prefix odh-pr --limit 10
uv run quay tags opendatahub/odh-autorag --commit 2fd3680
```

`axt` is the existing alias installed by the repository's `install.sh`.
Run `uv sync` after updating to install the entry point.

## Commands

| Command | Options |
|---|---|
| `latest IMAGE` | Exact `repository:tag` or `repository@sha256:…`; `--limit N` / `-n N` (default 1); `--output json\|table\|ref\|digest` |
| `search QUERY` | `--org NAMESPACE`, `--limit N`, `--all`, `--output json\|table` |
| `tags REPOSITORY` | `--prefix PREFIX`, `--match GLOB`, `--commit HEX`, `--include-artifacts`, `--limit N`, `--all`, `--output json\|table` |

Global `--json`, `--profile/-p`, and `--target/-t` work before or after the command.
`--json` remains available for scripts; JSON is already the default.
`--output table` explicitly requests a table.

### `latest`: exact images and tag history

`latest` replaces the former `inspect` command. Always supply an exact tag or
sha256 digest. It resolves that reference and retrieves the manifest by digest,
including manifest type and child platforms when available. It does not select
other tags from the repository. Migrate `quay inspect IMAGE` to `quay latest IMAGE`.
The former repository-wide `latest REPOSITORY --prefix/--match` syntax is removed.

With the default `-n 1`, a tag resolves its current active image and returns one
JSON image object. A digest directly resolves that image and has no tag history.

`-n N` / `--limit N`, with N greater than one, returns up to N **assignments of
that exact tag**, newest first, in a JSON list envelope with `items`, `returned`,
and `truncated`. For example, `:odh-pr-363 -n 3` returns this PR tag's history;
it does not search for other PR tags. A tag reassigned to the same digest later
is a separate history entry. Ordering follows Quay's tag-history order, preserving
ties. A deleted tag can still have retained historical assignments.

History is limited to what Quay retains and exposes. It is not a complete CI build
history: builds that never assigned the tag will not appear. If an older manifest
is unavailable, its assignment and digest remain in the result with
`manifest_available: false` and unavailable details left null. Other API errors
still fail the command. Listings are not atomic snapshots.

Use each record's `digest_ref` to identify that precise image. A `tag_ref` names
the mutable tag and can now point to another digest. `--output digest` emits one
pinned reference per returned assignment; `--output ref` is restricted to a single
tagged image. A digest input permits only `-n 1`.

### Discovery filters

`search` and `tags` default to 20 matching results; limits apply after filtering.
`--all` follows available pages. `--limit` and `--all` are mutually exclusive.
`tags` lists active tags in Quay's newest-updated-first order. That order does not
imply highest semantic version, newest Git commit, or successful CI.

Tag filters combine with AND. Prefix and shell glob matching are case-sensitive.
`--commit` accepts 7–64 hexadecimal characters and matches the beginning of a
hexadecimal token delimited by non-alphanumeric characters in a tag. Matching
ignores hexadecimal letter case and does not verify image provenance.

By convention, `sha256-*`, `*.sig`, `*.att`, `*.sbom`, and `*.git` are auxiliary
tags and hidden by `tags` by default. Use `--include-artifacts` to show them.
Exact `latest` resolution always permits auxiliary tags.

Repository paths may be nested or qualified with a host. Bare repository names
require a configured namespace; there is no implicit `opendatahub` namespace.
Search ignores the default namespace and spans organizations unless `--org` is
given. Quay can cap repository discovery even when no more pages are exposed.

## Configuration and private access

Anonymous access is the default. For private repositories, configure a **Quay API
bearer token** with repository read access (not Docker login credentials):

```yaml
profiles:
  dev:
    quay: odh
quay:
  odh:
    base_url: https://quay.io
    namespace: opendatahub
    token: ${QUAY_TOKEN}  # optional; omit for anonymous access
```

`config list`, `config show dev`, and `config validate` include Quay configurations.
Token values are masked by `config show`. Named targets take priority over profiles;
profiles follow the shared `AUTOX_PROFILE` and `defaults.profile` resolution rules.
If the service is unmapped, settings fall back to environment variables / `.env`:

| Variable | Default |
|---|---|
| `QUAY_BASE_URL` | `https://quay.io` (registry origin, no API path) |
| `QUAY_NAMESPACE` | None; required only for bare repository names |
| `QUAY_TOKEN` | None; anonymous access |

A named configuration uses its own token and defaults; environment credentials
are not merged into it. References naming another registry are rejected: choose
that registry's `--target` instead. Redirects are not followed. HTTP requests use
a 30-second timeout and at most three attempts for transport failures, HTTP 429,
and HTTP 5xx, with short exponential backoff.

## Agent-friendly output

JSON uses shared formatting. Image records include `repository`, `tag`, `digest`,
`tag_ref`, `digest_ref`, `updated_at` and `ended_at` (UTC ISO 8601), `is_active`,
`size` (bytes), `is_manifest_list`, `media_type`, and `platforms`. Missing metadata
is `null`. `latest` adds `manifest_available` to indicate whether manifest details
could be fetched. Unknown platform fields remain null (or the registry's literal
`unknown` value). Digest inputs leave tag fields, assignment timestamps, and
`is_active` null.

`latest -n 1` returns an image object; `latest -n N` with N greater than one returns
`items`, `returned`, and `truncated`, even when fewer than N assignments exist.
Discovery lists use the same envelope. `truncated: true` means known results were
omitted or search pagination stopped advancing. Search additionally returns
`search_may_be_capped: true`: `truncated: false` means all exposed results were read,
not that every repository was discoverable. JSON never includes a misleading total.

`--output ref` emits one tagged pull reference. `--output digest` emits
`host/repository@sha256:…` references, one per line. Explicit `--json` cannot be
combined with another output format. Diagnostics go to stderr.

| Exit code | Meaning |
|---|---|
| 0 | Success, including empty discovery lists |
| 1 | API or configuration failure |
| 2 | Invalid arguments or references |
| 3 | No matching image or retained tag history for `latest` |

PR discovery, build waiting, semantic-version selection, signature verification,
registry writes, and persistent caching are outside v1.
