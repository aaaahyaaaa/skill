# 运行时环境

运行时配置加载顺序为：先读取导出的进程环境变量，再读取显式 env 文件，最后读取 `config/runtime_defaults.json`。

重要变量：

- `OPEN_PLAT_TRACE_TOKEN`：OpenPlat trace token。通过导出的环境变量或宿主本地 env 文件提供。只保存原始 token，不带 `Bearer`，不要提交真实密钥。
- `OPEN_PLAT_TRACE_WORKSPACE_ID`：宿主 wrapper 可用的可选默认 workspace。
- `OPEN_PLAT_TRACE_DETAIL_URL`：默认值为 `http://zhishang.bytedance.net/open-plat/api/fornax/trace/detail`。
- `OPEN_PLAT_WORKSPACE_INFO_URL`：默认值为 `https://zhishang.bytedance.net/open-plat/api/workspace/get-workspace-info`；用于获取 Sirius recall 所需的 workspace `authInfo.apiKey`。
- `OPEN_PLAT_BOOTSTRAP_TOKEN`：workspace info 和 workflow replay 可用的可选备用 bootstrap token。
- `WORKFLOW_AUTH_TOKEN`：可选的直接 workspace apiKey fallback；设置后，`probe-wide-recall` 会在内存中用它调用 Sirius recall，workflow replay 也可能使用它。
- `BYTEDCLI_BIN`、`WORKFLOW_RDS_DATABASE`、`WORKFLOW_OPEN_EXEC_BASE_URL`：workflow node fetch / replay 依赖。
- `WIDE_RECALL_TOPK`：`probe-wide-recall` 的 topK 默认值；运行时仍会提升到至少 50。
- `KNOWLEDGE_DETAIL_URL`：知识详情接口，默认值为 `https://ad-sirius.bytedance.net/api/sirius_knowledge/v1/data/doc/record_id`。第一版支持 `GET /api/sirius_knowledge/v1/data/doc/record_id?source=...&identifier=...` 形态；也支持在 URL 中写 `{source}` / `{identifier}` 占位符。显式导出为空时，`probe-knowledge-detail` 只使用 trace/provided id 做三态判断。
- `KNOWLEDGE_DETAIL_TOKEN`：可选知识详情鉴权 token，不带 `Bearer`。未设置时依次复用 `OPEN_PLAT_TRACE_TOKEN`、`OPEN_PLAT_BOOTSTRAP_TOKEN`；CLI 只输出 token 来源，不输出 token 原文。
- `KNOWLEDGE_DETAIL_TIMEOUT_SECONDS`：可选知识详情 HTTP 超时，默认 20 秒。

使用 `python -m findreason schema` 查看 v3 命令契约。CLI 有意不提供会打印 token 的 env 命令。

Trace 查询失败属于证据采集失败。可能时，ingest 仍会输出 v3 JSON，并设置 `host_action_required=[replay-workflow]`。
