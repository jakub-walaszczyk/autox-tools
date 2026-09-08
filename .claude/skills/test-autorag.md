# Test AutoRAG Experiment

Run an end-to-end AutoRAG experiment: build a config, submit the pipeline, and analyze the outcome (diagnose failure or evaluate results). Use this to test a pipeline version, verify a bug fix, or benchmark a configuration.

## Input

`$ARGUMENTS` — which compiled pipeline YAML to use from `local/pipelines/`. The pipeline version is baked into the YAML file; this argument just selects which file. Examples:
- A filename: `pipeline_ai4rag_0.9.1.yaml`
- A substring to match: `sampling`, `refactored`, `0.9.1`
- A full path: `/path/to/custom_pipeline.yaml` (for pipelines outside the repo)
- Empty — list available pipelines and ask the user to pick one.

The user may also include inline hints about models, datasets, metric, or run name — extract them if present.

## Workflow

### Phase 1 — Resolve the pipeline

1. List available compiled pipelines:
   ```
   ls local/pipelines/*.yaml
   ```
2. Match `$ARGUMENTS` to a pipeline file (fuzzy substring match is fine). If ambiguous or empty, show the list and ask the user to choose.
3. Confirm the resolved pipeline path with the user before continuing.

### Phase 2 — Discover models and datasets

Run these in parallel to save time:

**Models** — discover what inference models are available on the OGX gateway:
```
uv run ogx models --json
```
Filter to chat/completion models (skip embedding models — those have a separate parameter). Present a short list of model IDs to the user and ask which to use, or whether to leave the default (pipeline's built-in default).

**Embedding models** — from the same `ogx models` output, filter to embedding models. Present them and ask the user to confirm the embedding model. Default: `vllm-embedding/bge-m3` (the most common in existing configs).

**Datasets** — check what test data and input documents exist in the user's S3 bucket:
```
uv run s3 list --recursive --json
```
or if the user has a known bucket, list its contents. Look for JSON benchmark files (test data) and document directories (input data). Present findings and ask the user to confirm or override. Defaults from existing configs:
- `test_data_bucket_name`: from profile config
- `test_data_key`: look for `benchmark_data.json` files
- `input_data_key`: look for `documents` directories

**Important:** Come to the user for explicit approval on models and datasets before proceeding. Show what you found and what you propose to use. Do not auto-select without confirmation.

### Phase 3 — Build the config

Generate a JSON config file at `local/configs/autorag-test-<timestamp>.json` following this structure (reference existing configs in `local/configs/` for the canonical format):

```json
{
  "pipeline_package": "<absolute-path-to-pipeline-yaml>",
  "experiment": "autox-tools-autorag",
  "run_name": "<descriptive-name-from-user-or-generated>",
  "parameters": {
    "test_data_secret_name": "minio",
    "test_data_bucket_name": "<bucket>",
    "test_data_key": "<path-to-benchmark-json>",
    "input_data_secret_name": "minio",
    "input_data_bucket_name": "<bucket>",
    "input_data_key": "<path-to-documents>",
    "ogx_secret_name": "ogx",
    "vector_io_provider_id": "milvus-remote",
    "embedding_models": ["<selected-embedding-model>"],
    "optimization_metric": "<metric>",
    "optimization_max_rag_patterns": 4
  }
}
```

Adjust parameters based on what the user specified and the pipeline's expected inputs. If the pipeline is known to accept additional parameters (check the YAML or prior configs for the same pipeline version), include them.

### Phase 4 — Validate and submit

1. **Dry-run validation** — confirm the config is well-formed:
   ```
   uv run pipelines run local/configs/<config-file> --dry-run
   ```
   Show the dry-run output to the user for final approval before submission.

2. **Submit the run:**
   ```
   uv run pipelines run local/configs/<config-file> --json
   ```
   Capture the `run_id` from the JSON output.

3. **Monitor the run** — check status periodically:
   ```
   uv run pipelines status <run-id> --json
   ```
   Report progress to the user. The run can take 10-60+ minutes. Tell the user you will check status and they can continue working. Check every few minutes until the run reaches a terminal state (succeeded or failed).

### Phase 5a — On FAILURE: Diagnose

If the run fails:

1. **Get the status summary** to identify which task(s) failed:
   ```
   uv run pipelines status <run-id>
   ```

2. **Fetch logs** from the failed tasks:
   ```
   uv run pipelines logs <run-id> --tail 200
   ```

3. **Analyze the logs:**
   - Identify the root cause (OOM, image pull error, parameter mismatch, model not found, S3 access denied, timeout, etc.)
   - Check if the error is in a specific pipeline component
   - Look for known error patterns from the AutoRAG/ai4rag stack
   - Present a clear diagnosis: what failed, why, and what to try next

4. **Suggest remediation** — concrete next steps (fix a parameter, check a secret, retry with different settings, etc.)

### Phase 5b — On SUCCESS: Analyze results

If the run succeeds:

1. **Fetch experiment results:**
   ```
   uv run autorag results <run-id> --detailed --json
   ```
   Parse and present: summary metrics, per-pattern leaderboard, best pattern configuration.

2. **Fetch artifact inventory:**
   ```
   uv run autorag artifacts <run-id>
   ```

3. **Inspect the best pattern's configuration:**
   ```
   uv run autorag artifacts <run-id> --pattern <best-pattern-name> --artifact pattern.json
   ```

4. **Inspect evaluation details:**
   ```
   uv run autorag artifacts <run-id> --pattern <best-pattern-name> --artifact evaluation_results.json
   ```

5. **Synthesize a verdict:**
   - Which pattern won and by how much?
   - What are the key metrics (faithfulness, answer_correctness, etc.)?
   - Are the scores reasonable for this dataset/model combination?
   - Any red flags (all patterns scoring identically, suspiciously perfect scores, etc.)?
   - If this was testing a bug fix: does the result confirm the fix works?

6. **If the user wants to compare** this run against a previous one, suggest:
   ```
   /compare-autorag <this-run-id> <other-run-id>
   ```

## Guidelines

- Always use `--json` flag when you need to parse output programmatically; use human-readable output when showing results to the user.
- Use the user's configured profile (`-p <profile>`) if they mention one, otherwise rely on the default profile.
- If any step fails unexpectedly, show the raw error output and ask the user how to proceed rather than guessing.
- Keep the user informed at each phase transition — don't go silent during long operations.
- The generated config file should be kept (not deleted) so the user can re-run or reference it later.
