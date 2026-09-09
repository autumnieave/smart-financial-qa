# API/接口层契约测试 B-19（2026-09-09）

> 结论：`tests/test_api_contracts.py` 以 FastAPI TestClient + stub pipeline 落地 **21 条契约基线**，覆盖 /health、/chat、/chat/stream（SSE 事件序列）、/chat/clarify 的成功路径、错误语义与请求校验；**零外部依赖**（不连 Qdrant / MySQL / LLM），已纳入 pytest 与 CI。pytest 全量 **244 passed**，覆盖率门禁实测 **86.33%**（B-20 基线 84.7%）。

## 1. mock 策略（保持零外部依赖）

- 导入 `app.api` 前替换 `core.pipeline.get_config / get_pipeline`，返回 `StubConfig + FakePipeline`，规避 RAGPipeline 真实装配（Qdrant/Embedding/BM25/引用语料预热）；
- `StubConfig.QUERY_CACHE_ENABLED=False`：不走 SQLite 查询缓存；`FakePipeline` 记录调用并返回固定结果，可通过标志注入故障 / 混合流；
- fixture 确保 `result/` 静态挂载目录在 CI（未跟踪目录）存在，结束清理。

## 2. 覆盖矩阵（21 条）

| 端点 | 场景 | 用例 |
| --- | --- | --- |
| /health | 200 + 集合名 | test_health_ok |
| /chat | rag / agent / 默认与 null mode / 字符串结果兼容 / 图片归一化与 http 直链 / references 透传 / 响应形状 | 9 条 |
| /chat | 422（缺 question、类型错误）、500（下游异常 → detail） | 3 条 |
| /chat/stream | rag：content+meta+done 事件序列；mixed：final 事件后重发终稿；agent：stage+done；agent 终稿不一致；error 事件；kwargs 透传；422 | 7 条 |
| /chat/clarify | 200（clarify_done=false）+ 调用记录、500、422 | 3 条 |

## 3. 验证数字

| 项 | 结果 |
| --- | --- |
| API 用例 | 21 passed（tests/test_api_contracts.py） |
| 全量 pytest | 244 passed |
| 覆盖率门禁（CI 口径） | 86.33%（--cov-fail-under=80 通过；B-20 基线 84.7%，API 用例同步覆盖 core.pipeline 等装配层） |

## 4. 说明与局限

- SSE 事件语义已验证：流式文本与终稿一致时不重复发送（direct）；不一致时先发 `final` 让前端重置再重发终稿（混合题路径）；agent 阶段事件（stage）即时反馈。
- 请求校验由 Pydantic（ChatRequest / ClarifyRequest）承接，422 语义与 FastAPI 默认一致。
- 未覆盖：真实服务集成（compose 起栈 → /health → 回放）、15s 编译超时语义的真实计时、并发/压力基线——归入 B-21 分层门禁与 Q5 计划。

## 5. 资产与命令

- 用例：`tests/test_api_contracts.py`；复跑：`python -m pytest tests/test_api_contracts.py -q`。
- 门禁复跑：`python -m pytest tests/ -q --cov=core --cov=tools.sql_guard --cov=tools.sql_validator --cov=pipelines.citation_validator --cov=utils.query_cache --cov-fail-under=80`。
