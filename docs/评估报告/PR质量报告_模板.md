# PR 质量报告（模板）

> 用途：一次 PR 一份，随 PR 评论或 CI artifact 留档，回答"这次改动是否可合"。
> 数据来源：第 1 层（pytest + 覆盖率 + ruff）→ 第 2 层（组件回归 + golden 子集 mock）→ 第 3 层（compose 冒烟，nightly）。
> 填写规则：只填实测数字；未跑的写"未启用 / 未跑"，不要留空或估算。

## 0. 元信息

| 项 | 值 |
| --- | --- |
| PR / commit | |
| 分支 / 目标分支 | |
| 改动范围（模块） | |
| 是否涉及 Prompt / golden / SQL 校验 | 是 / 否 |
| 本次跑到第几层 | L1 / L1+L2 / L1+L2+L3 |

## 1. 第 1 层：单测 + 覆盖率 + lint（必过）

| 指标 | 本次 | 基线 | 结论 |
| --- | --- | --- | --- |
| pytest 用例数 / 通过数 / 失败数 | | 347 passed（2026-09-10） | |
| 门禁口径覆盖率（core + sql_guard + sql_validator + citation_validator + query_cache） | | 84.7%（fail-under=80） | |
| ruff（E9 / F63 / F7 / F82）违规数 | | 0 | |

## 2. 第 2 层：组件回归 + golden 子集（mock 版，离线）

| 指标 | 本次 | 基线 | 结论 |
| --- | --- | --- | --- |
| 组件回归（sql_guard / sql_validator / metric_consistency） | | 全绿 | |
| golden 子集题数（seed 固定） | | 20 题 | |
| 子集语句级解析通过率 | | 100% | |
| 子集题级全通过 | | 20/20 | |

> 口径提示：第 2 层是 **mock 版**（参考 SQL 解析 + 字段归属静态校验），不含 LLM 生成与 MySQL 编译，**不可**与 golden 全量真实口径（99/99、137/137）直接对比。

## 3. 第 3 层：compose 冒烟（nightly / 发布前）

| 检查 | 结果 | 备注 |
| --- | --- | --- |
| `docker compose up -d`（qdrant / backend / frontend） | | |
| `http://localhost:6333/healthz` | | |
| `http://localhost:18000/health` | | |
| 单题问答冒烟（`-WithChatSmoke`，消耗额度） | | 默认关闭 |

## 4. 质量判定

| 判定项 | 结论 |
| --- | --- |
| 是否通过门禁 | 通过 / 不通过 |
| 是否需人工复核（Prompt / golden / 评测口径变更） | 是 / 否 |
| 遗留风险与跟进任务编号 | |

## 5. 证据链接

- CI run：
- 产物 JSON：`训练结果数据/ci_golden_subset.json`
- 冒烟报告：`训练结果数据/ci_compose_smoke_<ts>.json`