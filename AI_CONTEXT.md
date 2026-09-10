# AI_CONTEXT.md —— 项目记忆锚点

> 生成时间：2026-09-09（项目现状深度扫描）；**更新：2026-09-10（B-13~B-31 落地后口径刷新；B-30 一致性校验已闭环）**；证据来源见各小节引用的文件与行号。

## 1. 当前系统状态摘要

- 上市公司“智能问数”助手：用户用自然语言查财务数据（SQL 链路）与问研报观点（RAG 链路），答案带引用溯源，由 LangGraph supervisor-workers 多 Agent 编排（财务 / 研报子 Agent 并行）。
- 技术栈：Python 3.11 + FastAPI（REST / SSE）+ React 前端 + LangGraph + Qdrant + MySQL + SQLite 记忆；模型与 Embedding 走 DashScope（qwen3.5-plus / qwen3-rerank / text-embedding-v2）。
- 近 10 次提交主线：B-13 prompts 版本注册表 → B-14 三层结构 → B-15/16 few-shot 与动态检索 → B-17 输出契约 → B-18 兜底话术 → B-19/20 API 契约与覆盖率门禁 → B-22 对抗挑战集 v2（18 条/5 类，真实执行 + 人工抽审）→ B-26~B-29 挑战集暴露缺陷修复（supervisor 误拒答 / 注入先拒答 / 错别字归一化 / 研报预测荐股转述口径）→ B-31 库外指标统一出口（unsupported_metrics + refuse.metric_out_of_scope，C2007/C2016/C2017 闭环）→ B-30 指标-问题一致性校验（SELECT 指标列 ⊆ standard_fields，越界走 refuse.metric_mismatch）。
- 离线单测 **347 passed**（31 个 test_*.py + conftest，零外部依赖）；挑战集证据支持 `python -m eval challenge --rejudge` 零成本重判（不重跑 LLM），人工复核存 sidecar（训练结果数据/challenge_v2_review.json）；竞赛原始数据与 golden 基准本地保留、不入库。

## 2. 确定性锚点（已知稳定项）

- SQL 守卫族（tools/sql_validator.py / sql_guard.py / financial_mean_guard.py）——B-07~B-12 均以“规则守卫 + 单测”双重闭环，SQL 编译通过率 100% 基线稳定。
- L1 引用核验（pipelines/citation_validator.py + tests/test_citation_validator.py）——自动核验 + 报告证据链，文件可溯源 1080/1080 两次复验一致。
- 缓存与记忆基础设施（utils/query_cache.py、memory/store.py 的 SQLite 后端）——有独立单测（test_query_cache / test_memory / test_pipeline_memory）覆盖。
- 分块与表聚合（data/splitter.py、utils/helpers.py + test_splitter / test_table_agg_topk）——overlap=100 经 5 档对比实验固化，代码 / config / pipeline 三处口径统一。
- 离线评估入口（eval/runner + golden v1：80 题 / 108 子问题 / 291 句，不可变快照 + sha256 防篡改；golden v2 对抗挑战集 18 条/5 类，2026-09-10 按 B-29 方案 B 修订 C2018 后重固化）。
- prompts 版本注册表（prompts/registry.json，7 条登记：6 业务模块 + 包级；当前包级 2026-09-10-v6、multi_agent v3、fallback v4、financial v9）+ 防漂移单测（tests/test_prompt_registry.py）。
- 出口守卫族（agents/langgraph_multi_agent.py：`_guard_injection_prefix` 注入先拒答、`_guard_research_advice` 研报预测/评级转述口径 + 免责）+ 单测 27 例。

## 3. 风险与债务清单

- 接口“绕道”均为构造 / 离线 / 评测场景直连具体类：pipelines/rag_pipeline.py:35、42，eval/retrieval_cmp.py:144，tools/data_scripts/rebuild_full_index.py:32；在线问答主路径经 IRetriever 接口，未见违规。
- 硬编码长 Prompt 集中在离线数据脚本（tools/data_scripts/pdf处理+校验入库.py:156、543，重抽取.py:92），未纳入 prompts/ 版本管理；app / agents / pipelines 在线代码无硬编码 system prompt。
- 缓存污染风险：/chat 缓存 key 不含 Prompt / Query 版本（app/api.py:86、124），QUERY_CACHE_VERSION 默认空；财务链路缓存 key 已含 FINANCIAL_PROMPT_VERSION，无此风险。
- Redis 记忆后端代码可用但无 docker-compose 编排（compose 仅 qdrant / backend / frontend），MEMORY_REDIS_URL 默认 localhost:6379/0 连通性需人工验证；评测与重建脚本集合默认值（research_reports_v3）与 config 默认（research_reports_v3_full）不一致，存在跑错集合风险。
- 测试健康：347 passed、0 失败 0 跳过；源码 TODO / FIXME / HACK 为 0 处；prompts 模块级版本号已补齐（B-13 起，registry.json 为唯一事实源）。
- 挑战集复跑新发现的缺陷（2026-09-10）与处置：B-31 已闭环——C2016「每股公积金」的近似字段替换（net_asset_per_share 答 24.00 元）与 C2007 股价/总市值回落技术性 SQL 报错，均由「库外指标统一出口」修复（契约扩为五键 unsupported_metrics，tools/native_financial.py:unsupported_metrics_of + prompts/fallback.py:build_refuse_metric_out_of_scope）；复跑 auto 15/15 pass、0 fail、3 pending（仅 binding 恒 pending）。B-30（指标口径防线）已于同日闭环：**拦截层**=库外指标出口（unsupported_metrics + 库外词表 `_OUT_OF_SCOPE_QUESTION_TERMS` 兜底，词表已比对 golden 80 题与挑战集无其他命中）；**审计层**=`metric_field_consistency_error`（SELECT 指标列 vs standard_fields，只记录不拦截——首轮 80 题全量回归 94 次判定 6 次命中，逐条核验均为计划欠规范而非真实替换，硬拦截曾使 B2076 退化为仅研报回答），10 条单测；**80 题全量回归（审计版）语句级编译 99/99 = 100%、失败 0、平均 42.7s/题**，审计信号 92 次判定 1 次命中（计划欠规范），兜底出口 12 次（库外 7 + 词表兜底 1「市值」+ 空结果 3 + 建议 1）；同口径对照确认硬门禁版曾丢 B2031/B2033/B2035/B2076 的 SQL，审计版全部恢复。新发现指标库内外判定运行间波动（B2075/B2069）→ 登记 B-32；C2018 研报召回不稳定（3 次运行仅 1 次召回预测/评级段落）→ 归入 B-24 检索质量评估证据。

## 4. 下一次迭代的检查建议

- 改动财务 / SQL 链路先回归 test_financial_mean_guard + test_sql_guard + test_sql_validator + test_metric_standardize；改编排回归 test_langgraph_planner / test_langgraph_multi_agent；合入前全量 `pytest tests/ -q`。
- 优先修复：为 /chat 缓存 key 并入 PROMPT_VERSION / QUERY_CACHE_VERSION，或对 RAG / Agent 模式默认关闭结果缓存。
- 统一评测与重建脚本的集合默认值为 research_reports_v3_full（或全部改读 config/rag_config.py），消除跑错集合风险。
- 将数据脚本长 Prompt 收口到 prompts/（至少登记版本与用途），并明确 Redis 是否启用——不启用则在文档标注“代码支持、环境未落地”。
- 指标库内外判定波动（B-32：B2075/B2069 全量判库外拒答、复跑可出数）与挑战集 pending 项（仅剩绑定 3 条，恒 pending）在 B-25 LLM-judge 与人工双回查对齐 ≥90% 后启用自动判定（§6.7.3）。
- 改动财务链路时除既有回归集外，另看 output_contract_stats.jsonl 的 kind=metric_consistency 命中率（计划↔SQL 字段差异，供人工判定是欠规范还是替换）；下一优先项：B-24 检索质量评估（补 C2018 召回证据）与 B-25 LLM-judge 校准。
