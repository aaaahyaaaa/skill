# 表象到根因对照 v4

本文件吸收用户指定飞书文档第三节“详细「表象 -> 根因」对照表（含真实 case 表象）”的方法：每一行都从具体现象出发，给出候选根因和验证方法。它不是 cause 枚举，也不是硬规则表。

## Preprocess 候选

| 表象 | 候选根因 | 验证 |
|-|-|-|
| 用户带场景上下文，但答案给通用或其他场景入口 | 多模态/上下文约束未进入 Workflow 输入 | 对比用户原问题、评估器上下文与 `workflow_span_ios[].input` |
| 答案多了或少了限定词，宽窄范围变化 | rewrite query 漂移 | 检查 `rewrite_query` / `keywords` 是否擅自增删场景词 |
| 多端、多账号类型、多子问题只答一种 | 关键实体丢失或多意图未拆解 | 检查 keywords 是否保留实体；用拆解 query 跑 recall 实验 |

## Knowledge 候选

| 表象 | 候选根因 | 验证 |
|-|-|-|
| 答案说查不到更深解释或官方日期 | KB 缺主题或深度内容 | open-label wide recall topK>=50 仍无支撑 |
| 评估器反复说资料未提及 | KB 无权威依据支撑断言 | 检查宽召回和权威/citable 来源 |
| 相邻主题命中但事实张冠李戴 | KB 只有相邻主题，缺精确主题 | 检查 matched docs 是否正文支撑精确断言 |
| 同一事项前后口径冲突 | KB 自身冲突且无适用前提 | 检查同主题文档是否存在对立结论和消歧条件 |

## Retrieval 候选

| 表象 | 候选根因 | 验证 |
|-|-|-|
| 答案说没查到，但 KB 实际有文档 | recall 漏召 | 宽召回命中、`origin_doc_list/origin_faq_list` 未命中 |
| 漏答子问题，子问题文档在库里 | 子主题 query 未进入 recall | 用子问题 query 跑 recall 对照 |
| 正确文档被权限/标签过滤 | ACL / namespace / label 隐藏 | 开放标签可见但当前 workspace/app/user 路径不可见 |

## Rerank 候选

| 表象 | 候选根因 | 验证 |
|-|-|-|
| 场景文档 recall 有，rerank 后没了 | rerank 把场景相关文档排出 topK | 比较同断言支撑在 recall 与 rerank 的生存状态 |
| 多子问题只答其一，另一支撑在 recall | rerank 去重/topK 误杀次主题 | 检查次主题文档 rank/score movement |
| 泛主题文档压过精确文档 | rerank 偏向高频或标题词面 | 观察精确文档分数异常和可恢复实验 |

## Answer 候选

| 表象 | 候选根因 | 验证 |
|-|-|-|
| prompt 有全部要点但答案漏答 | 模型未覆盖 prompt 中已有要点 | prompt_docs 支撑、answer 未输出 |
| 答案超出问题范围 | 模型未遵守场景约束 | 上游有窄场景证据，答案扩成通用结论 |
| 答案自相矛盾 | 模型混用分支或前后不一致 | KB/prompt 已澄清前提，answer 未区分 |
| 引用不支持结论 | wrong citation | 引用文档存在但内容不支持 claim |
| 事实与权威来源冲突 | unsupported claim / 过度推断 | prompt 支持正确答案，模型给出冲突断言 |

## 使用方式

报告中不要把这些行当作标签枚举。应该写成：

```text
我看到的表象是 A。
它可能由 X/Y/Z 解释。
目前 trace 支持 X 的证据是...
但如果实验 E 命中，就会反驳 X 并支持 Y。
```
