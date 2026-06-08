# AIME / 宿主 Agent 迁移说明 v3

迁移时需要带上完整 skill 目录，包括：

- `SKILL.md`
- `findreason/`
- `scripts/findreason.py`
- `scripts/findreason_core/`
- `references/`
- `requirements.txt`
- `config/runtime_defaults.json`

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

`config/runtime_defaults.json` 不应包含真实 token。通过导出的环境变量或宿主本地 env 文件提供 `OPEN_PLAT_ZS_OPEN_TOKEN`，值不带 `Bearer` 前缀；不要再使用旧 token 变量名。

当宿主需要使用不同 token 或 endpoint 时，导出的环境变量仍然优先于项目默认配置。
