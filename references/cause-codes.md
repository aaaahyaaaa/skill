# v3 Cause Codes

`candidate_cause` must be one of these 13 values.

| Stage | cause_code | Owner | Required condition |
|-|-|-|-|
| preprocess | `non_rag_route_boundary` | agent_router_owner | Case is not knowledge QA or should route outside RAG. |
| preprocess | `query_rewrite_drift` | rag_preprocess_or_workflow_owner | Rewrite changes the user intent and affects downstream retrieval. |
| preprocess | `keyword_loss` | rag_preprocess_or_workflow_owner | Key entity/phrase is dropped before retrieval. |
| knowledge | `suspected_knowledge_missing` | kb_owner | `knowledge_exists=no`; do not use when state is `unknown`. |
| knowledge | `knowledge_topic_mismatch` | kb_owner | TopK has adjacent-topic docs but lacks the exact topic. |
| retrieval | `retrieval_miss` | retrieval_strategy_owner | Knowledge exists but online recall misses expected docs. |
| retrieval | `permission_miss` | knowledge_permission_owner | Correct knowledge exists but ACL/namespace hides it online. |
| rerank | `rerank_drop` | rerank_strategy_owner | Recall hit exists but expected doc does not survive rerank. |
| rerank | `rerank_tunable` | rerank_strategy_owner | Parameter/tunable evidence shows rerank can recover the doc. |
| context | `context_assembly_error` | workflow_or_prompt_context_owner | Expected doc survives rerank but is absent from prompt context, or context is truncated/noisy. |
| answer | `unsupported_claim` | prompt_or_model_owner | `prompt_supports_answer=true`, `answer_satisfies_expected=false`, and unsupported claims exist. |
| answer | `wrong_citation` | prompt_or_model_owner | Answer cites the wrong document under the answer-stage precondition. |
| answer | `partial_answer` | prompt_or_model_owner | Prompt supports a complete answer, but output omits required aspects. |

Answer causes require both `qa.prompt_supports_answer=true` and `qa.answer_satisfies_expected=false`. If `prompt_supports_answer=false`, primary cause must stay upstream.

Evaluation is observation-only in v3 and has no official cause code.
