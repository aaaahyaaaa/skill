# env 历史废弃说明

此文件只保留历史废弃说明，不再作为当前运行配置入口。

FindReason CLI 的鉴权与端点类运行配置已经改为源码固定常量。运行时不会读取本地 env 文件，也不会通过 shell 环境变量覆盖 token、OpenPlat endpoint、workspace info endpoint、workflow endpoint/database 或 knowledge detail endpoint。

当前使用者只需要提供每个 case 自身的输入，例如 `workspace_id`、`app_id`、`log_id`、`case_file`、`output_dir`，以及 trace 缺失时 replay 需要的真实 `query`。
