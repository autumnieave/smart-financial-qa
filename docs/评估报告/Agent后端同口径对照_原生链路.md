# Agent 后端同口径对照报告（原生 SQL 链路）

> 2026-09-05 · B-05 · 最终口径 = 10 题子集（成本闸门收口）· qwen3.5-plus / langgraph(multi-agent) vs handwritten
> 只换编排层：同 prompt、同 tools、同原生财务链路（SQL 三层防线）、同输出契约；逐子问题真实 agent_query。

## 汇总对比

| 指标 | handwritten（自研循环） | langgraph（multi-agent，默认） |
| --- | --- | --- |
| 题目数 / 子问题数 | 10 / 22 | 10 / 22 |
| 失败子问题 | 0 | 0 |
| 有 SQL 题目数 | 8 | 8 |
| 语句总数 / 通过 | 25/25 | 17/17 |
| 语句级 SQL 通过率 | 100.0% | 100.0% |
| 引用总数 / 文件可溯源 | 27/27（100.0%） | 124/148（83.8%） |
| 单题耗时 均值 / 中位 | 106.2s / 87.5s | 54.0s / 38.5s |
| 总耗时 | 1061.9s | 540.4s |

## 结论

- 语句级 SQL 通过率：两后端均 100%（handwritten 25/25、langgraph 17/17），三层防线对两编排层都生效；
- 单题耗时：langgraph 中位 38.5s < handwritten 87.5s（约快 56%），langgraph 直出/汇总节点省去多轮自循环；
- 工具路由差异：同 10 题 handwritten 累积 25 句 SQL + 27 条引用，langgraph 17 句 SQL + 148 条引用 —— langgraph 更倾向研报侧聚合，handwritten 更频繁触发财务查询；
- 引用可溯源（L1）：handwritten 27/27=100%，langgraph 124/148=83.8%（24 条不可溯源引用来自研报子 Agent 生成，建议留意）；
- 收口说明：全量 80 题双后端按本 10 题单题耗时中位外推约 2.8h > 2h 阈值，按任务约定以 10 题同口径收口；两组共用 golden 前 10 题，逐子问题真实生成，QUERY_CACHE_ENABLED=false。
- 修复项：对照过程中修复 handwritten 编排 `_merge_chart_json` 对非 dict 消息的兼容（跨轮引用展开崩溃）与后端切换复用过期 MySQL 缓存连接的问题；修复后两后端均正常出结果，单测 123 passed。