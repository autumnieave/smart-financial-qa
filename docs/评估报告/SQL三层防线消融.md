# SQL 三层防线消融实验报告（原生链路 · 函数级单发口径）

> 2026-09-05 · B-04 · qwen3.5-plus（enable_thinking=False，temperature=0.1，max_tokens=800）
> 口径：golden v1 全部子问题逐子问题独立生成（不做 Agent 多轮累积），与 2026-08“单发复跑”口径一致；
> 空 SQL（LLM 判定无需 DB 查询）不计语句分母；防线反馈重试 ≤2 次（同 AGENT_NATIVE_RETRY=2）。

## 四组防线组合

| 组 | 构成 | 执行闸门 |
| --- | --- | --- |
| 仅提示词 | SQL_GEN_SYSTEM_PROMPT（字段-表归属等规则） | 无，生成即执行（编译仅测量） |
| +静态校验 | 提示词 + validate_sql | 静态校验；通过后执行（编译仅测量） |
| +编译重试 | 提示词 + MySQL compile_check | 编译通过才执行 |
| 全量（现状） | 提示词 + 静态校验 + 编译重试 | 静态 + 编译双闸门 |

## 语句级结果对比

| 组 | 有 SQL 子问题 | 编译通过 | 语句级编译通过率 | 编译测量失败 | 被静态拦(重试耗尽) | 被编译拦(重试耗尽) | 静态拦次数(尝试级) | 编译拦次数(尝试级) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 仅提示词 | 108 | 101 | 93.5% | 7 | 0 | 0 | 0 | 7 |
| +静态校验 | 108 | 108 | 100.0% | 0 | 0 | 0 | 6 | 0 |
| +编译重试 | 108 | 108 | 100.0% | 0 | 0 | 0 | 0 | 6 |
| 全量（现状） | 108 | 108 | 100.0% | 0 | 0 | 0 | 4 | 0 |

> 说明：编译通过率分母 = 该组首轮生成过非空 SQL 的子问题数（空 SQL 不计）。
> “编译测量失败”= 已过本组闸门进入执行模拟、但 MySQL 编译不过（仅提示词/静态组可能出现，即编译层需兜底的漏网）；
> “被 X 拦(重试耗尽)”= 该闸门重试 2 次仍不通过，语句不会进入执行；拦截次数为尝试级计数（含拦截后修复放行）。

## 分层归因样例

### 仅提示词组（基线：编译不过的语句 = 后两层需拦截对象）

- **B2008**（归因分析）: `SELECT t1.stock_code, t1.stock_abbr, t1.report_year, t1.report_period, t1.total_operating_revenue, t1.net_profit_10k_yuan, t1.roe, t1.gross_profit_mar` → (1054, "Unknown column 't1.asset_total_assets' in 'field list'")
- **B2010**（归因分析）: `SELECT stock_code, stock_abbr, report_year, report_period, total_operating_revenue, operating_revenue_yoy_growth, operating_expense_cost_of_sales, ope` → (1054, "Unknown column 'operating_expense_cost_of_sales' in 'field list'")
- **B2017**（融合查询）: `SELECT AVG(t1.net_profit_yoy_growth) FROM core_performance_indicators_sheet t1 JOIN stock_info t2 ON t1.stock_code = t2.stock_code WHERE t2.industry_n` → (1146, "Table 'financial_database.stock_info' doesn't exist")
- **B2045**（多意图）: `SELECT stock_abbr, asset_liability_ratio, roe FROM balance_sheet t1 JOIN core_performance_indicators_sheet t2 ON t1.stock_code = t2.stock_code AND t1.` → (1052, "Column 'stock_abbr' in field list is ambiguous")
- **B2049**（多意图）: `SELECT t1.stock_code, t1.stock_abbr, t1.operating_expense_selling_expenses, t1.total_operating_revenue, t2.net_profit FROM income_sheet t1 JOIN core_p` → (1054, "Unknown column 't2.net_profit' in 'field list'")
- **B2071**（归因分析）: `SELECT t1.roe, t2.asset_liability_ratio, t2.equity_total_assets, t3.operating_expense_cost_of_sales, t3.operating_expense_selling_expenses, t3.operati` → (1054, "Unknown column 't2.equity_total_assets' in 'field list'")

### +静态校验组（残留 = 静态拦不住、须编译层兜底）

- （无失败）

### +编译重试组

- （无失败）

### 全量组（现状）

- （无失败）

### 各层拦截 -> 反馈修复样例（层贡献证据）

- 以下为闸门首轮拦截到错误、把错误反馈给 LLM 后重试修复放行的实例（统计见上表“拦截次数(尝试级)”）。

**静态层拦截-修复**

- B2008（归因分析）首错：['字段 `asset_total_assets` 不存在于表 `core_performance_indicators_sheet`（别名 t1）'] → 修复：`SELECT t1.stock_abbr, t2.asset_total_assets, t2.asset_liability_ratio, t3.net_cash_flow FROM core_performance_indicators_sheet t1 JOIN balance_sheet t2 ON t1.st`
- B2009（归因分析）首错：['字段 `asset_total_assets_yoy_growth` 不存在于 FROM 中的任何表'] → 修复：`SELECT t1.asset_total_assets_yoy_growth, t2.liability_total_liabilities_yoy_growth, t3.net_cash_flow_yoy_growth FROM balance_sheet t1 JOIN balance_sheet t2 ON t`
- B2014（多意图）首错：["SQL 结构解析失败：SELECT '数据来源于核心业绩指标表、资产负债表、现金流量表和利润表' AS data_source"] → 修复：`SELECT stock_code, stock_abbr, report_year, report_period FROM core_performance_indicators_sheet LIMIT 1;`
- B2049（多意图）首错：['字段 `net_profit` 不存在于表 `core_performance_indicators_sheet`（别名 t2）'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t1.operating_expense_selling_expenses, t1.total_operating_revenue, t2.net_profit FROM income_sheet t1 JOIN income_sheet t2 `

**编译层拦截-修复**

- B2008（归因分析）首错：['(1054, "Unknown column \'t1.asset_total_assets\' in \'field list\'")'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t1.report_year, t1.report_period, t2.asset_total_assets, t2.liability_total_liabilities, t2.equity_total_equity, t3.total_o`
- B2009（归因分析）首错：['(1054, "Unknown column \'asset_total_assets_yoy_growth\' in \'field list\'")'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t1.report_year, t1.report_period, t1.asset_total_assets_yoy_growth FROM balance_sheet t1 WHERE t1.report_period = 'FY' ORDE`
- B2045（多意图）首错：['(1052, "Column \'stock_abbr\' in field list is ambiguous")'] → 修复：`SELECT AVG(t1.roe) AS avg_roe_low_leverage FROM core_performance_indicators_sheet t1 JOIN balance_sheet t2 ON t1.stock_code = t2.stock_code AND t1.report_year =`
- B2049（多意图）首错：['(1054, "Unknown column \'t2.net_profit\' in \'field list\'")'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t1.operating_expense_selling_expenses, t1.total_operating_revenue, t2.net_profit_10k_yuan FROM income_sheet t1 JOIN core_pe`

**全量组拦截-修复**

- B2045（多意图）首错：['字段 `stock_abbr` 在表（balance_sheet, core_performance_indicators_sheet）中同时存在，需加表别名消除歧义'] → 修复：`SELECT t1.stock_abbr, t1.asset_liability_ratio, t2.roe FROM balance_sheet t1 JOIN core_performance_indicators_sheet t2 ON t1.stock_code = t2.stock_code AND t1.r`
- B2049（多意图）首错：['字段 `net_profit` 不存在于表 `core_performance_indicators_sheet`（别名 t2）'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t2.operating_expense_selling_expenses, t2.total_operating_revenue, t3.net_profit FROM core_performance_indicators_sheet t1 `
- B2073（归因分析）首错：['SQL 结构解析失败：SELECT 1'] → 修复：`SELECT stock_code, stock_abbr, report_year, report_period FROM core_performance_indicators_sheet LIMIT 1;`
- B2077（开放性问题）首错：['字段 `operating_expense_cost_of_sales` 不存在于表 `core_performance_indicators_sheet`（别名 t1）'] → 修复：`SELECT t1.stock_code, t1.stock_abbr, t1.report_year, t1.report_period, t2.operating_expense_cost_of_sales, t2.total_operating_revenue, t3.asset_total_assets FRO`


## 结论

- 三层防线逐层收敛：提示词规则消解大部分字段-表归属错误；静态校验在执行前拦截表名/别名/归属类错误（免 MySQL 往返）；
- MySQL 编译重试兜底静态校验边界（函数调用/复杂子查询/MySQL 特有语法等），使放行语句全部真实编译通过；
- 全量组（现状）结果与 2026-08 全量回归“修复后 100%”口径对齐。