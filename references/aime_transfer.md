# AIME / Agent Host Transfer v3

Ship the whole skill directory, including:

- `SKILL.md`
- `findreason/`
- `scripts/findreason.py`
- `scripts/findreason_core/`
- `references/`
- `requirements.txt`
- `config/runtime_defaults.json`

The host should call `python -m findreason`.

## Required Sequence

```bash
python -m findreason ingest-fornax-trace \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file <case.json> \
  --output-dir <case_dir>
```

Run the recommended probes from `ingest_summary.suggested_probe_set`, writing results to `<case_dir>/probes/`.

```bash
python -m findreason orchestrate \
  --ingest-file <case_dir>/ingest.json \
  --probe-dir <case_dir>/probes \
  --mode final \
  --schema-version v3 \
  --output-dir <case_dir>/final
```

## Host-Owned Work

The target host Agent owns input extraction, judgement compression, unsupported-claim extraction, wrong-citation extraction, answer alignment, batch fan-out, and final report rendering.

## Configuration

`config/runtime_defaults.json` should not include real tokens. Provide `OPEN_PLAT_TRACE_TOKEN` through exported environment variables or a host-local env file, without the `Bearer` prefix.

Exported environment variables still override project defaults when a host needs a different token or endpoint.
