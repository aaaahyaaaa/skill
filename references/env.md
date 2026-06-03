# Runtime Environment

Runtime config loads exported process variables first, then explicit env files, then `config/runtime_defaults.json`.

Important variables:

- `OPEN_PLAT_TRACE_TOKEN`: OpenPlat trace token. Provide it through exported environment variables or a host-local env file. Store the raw token only, without `Bearer`, and do not commit real secrets.
- `OPEN_PLAT_TRACE_WORKSPACE_ID`: optional default workspace for host wrappers.
- `OPEN_PLAT_TRACE_DETAIL_URL`: defaults to `http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail`.
- `OPEN_PLAT_WORKSPACE_INFO_URL`: defaults to `https://zhishang.bytedance.net/open-plat/api/workspace/get-workspace-info`; used to fetch the workspace `authInfo.apiKey` for Sirius recall.
- `OPEN_PLAT_BOOTSTRAP_TOKEN`: optional alternate bootstrap token for workspace info and workflow replay.
- `WORKFLOW_AUTH_TOKEN`: optional direct workspace apiKey fallback; when set, `probe-wide-recall` uses it in memory for Sirius recall and workflow replay may also use it.
- `BYTEDCLI_BIN`, `WORKFLOW_RDS_DATABASE`, `WORKFLOW_OPEN_EXEC_BASE_URL`: workflow node fetch/replay dependencies.
- `WIDE_RECALL_TOPK`, `KNOWLEDGE_DETAIL_URL`: optional probe integrations.

Use `python -m findreason schema` to inspect the v3 command contract. The CLI intentionally does not expose a token-printing env command.

Trace lookup failures are evidence collection failures. Ingest still emits v3 JSON with `host_action_required=[replay-workflow]` when possible.
