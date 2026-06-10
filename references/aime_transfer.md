# AIME / 宿主 Agent 迁移说明 v3

迁移时需要带上完整 skill 目录，包括：

- `SKILL.md`
- `findreason/`
- `scripts/findreason.py`
- `scripts/findreason_core/`
- `references/`
- `requirements.txt`

宿主应通过 `python -m findreason` 调用。

## 必须执行的顺序

```bash
python -m findreason ingest-fornax-trace \
  --workspace-id <workspace_id> \
  --log-id <log_id> \
  --app-id <app_id> \
  --case-file <case.json> \
  --output-dir <case_dir>
```

读取 `ingest_summary.suggested_probe_set`，运行推荐 probes，并将结果写入 `<case_dir>/probes/`。

```bash
python -m findreason orchestrate \
  --ingest-file <case_dir>/ingest.json \
  --probe-dir <case_dir>/probes \
  --mode final \
  --schema-version v3 \
  --output-dir <case_dir>/final
```

## 宿主负责的工作

目标宿主 Agent 负责输入抽取、judgement 压缩、unsupported claim 抽取、wrong citation 抽取、answer alignment、批量 fan-out 和最终报告渲染。

## 配置

当前 CLI 使用源码固定常量，不再读取 `config/runtime_defaults.json`、`.env.local` 或导出的环境变量来改写 token / endpoint。宿主需要更换 token 或 endpoint 时，必须修改对应 CLI 源码常量并重新验证。
