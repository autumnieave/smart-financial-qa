# -*- coding: utf-8 -*-
"""tests/test_financial_mean_guard.py —— 分析层“行业均值”口径纯函数单测（零外部依赖）。

覆盖 tools/native_financial.py 的：
- _mean_column_for_question / _wants_aggregate_mean / _company_rows：均值意图与字段识别
- _industry_mean_hint：多公司明细时生成行业均值口径提示（仅比率字段）
- _mean_consistency_error：分析文本与均值口径的自洽性核验
"""

from tools.native_financial import (
    _company_rows,
    _industry_mean_hint,
    _mean_column_for_question,
    _mean_consistency_error,
    _mean_ratio_equivalence_issue,
    _wants_aggregate_mean,
)


def _rows(*items):
    return [dict(pair) for pair in items]


ROWS_73 = [
    {"stock_abbr": "昭衍新药", "asset_liability_ratio": 14.81, "liability_total_liabilities": 141236.05, "asset_total_assets": 953338.27},
    {"stock_abbr": "药明康德", "asset_liability_ratio": 24.4525, "liability_total_liabilities": 2313363.81, "asset_total_assets": 9460613.54},
    {"stock_abbr": "成都先导", "asset_liability_ratio": 22.56, "liability_total_liabilities": 43673.77, "asset_total_assets": 193564.16},
]


class TestMeanIntent:
    """均值意图与字段识别。"""

    def test_asset_liability_keyword(self):
        assert _mean_column_for_question("计算2025年第三季度66家公司的资产负债率行业均值") == "asset_liability_ratio"

    def test_formula_validation_keyword(self):
        assert _mean_column_for_question("计算结果是否符合“负债总额/资产总额”") == "asset_liability_ratio"

    def test_roe_keyword(self):
        assert _mean_column_for_question("行业平均净资产收益率") == "roe"

    def test_no_ratio_keyword(self):
        assert _mean_column_for_question("25年各中药企业研发情况") is None

    def test_wants_aggregate(self):
        assert _wants_aggregate_mean("计算行业均值")
        assert _wants_aggregate_mean("是否符合负债总额/资产总额")
        assert not _wants_aggregate_mean("华润三九2023年利润是多少")


class TestCompanyRows:
    """公司行计数。"""

    def test_multiple_companies(self):
        assert _company_rows(ROWS_73) == 3

    def test_aggregate_row_without_company(self):
        assert _company_rows([{"AVG(asset_liability_ratio)": 30.62}]) == 0

    def test_single_company(self):
        assert _company_rows([{"stock_abbr": "广誉远", "roe": 5.0}]) == 1


class TestIndustryMeanHint:
    """多公司明细下生成行业均值口径提示。"""

    def test_hint_computed_over_all_rows(self):
        hint = _industry_mean_hint("计算2025年第三季度66家公司的资产负债率行业均值", ROWS_73)
        assert hint is not None
        assert "3 家" in hint
        mean = (14.81 + 24.4525 + 22.56) / 3
        assert f"{mean:.2f}" in hint

    def test_hint_for_formula_validation(self):
        hint = _industry_mean_hint("计算结果是否符合“负债总额/资产总额”", ROWS_73)
        assert hint is not None
        assert "asset_liability_ratio" in hint

    def test_hint_without_company_labels(self):
        """明细行无 stock_abbr 标签（SELECT 未带公司列）且无时间列时，仍按多公司样本求均值。"""
        rows = [
            {"asset_liability_ratio": 14.81, "liability_total_liabilities": 141236.05, "asset_total_assets": 953338.27},
            {"asset_liability_ratio": 24.4525, "liability_total_liabilities": 2313363.81, "asset_total_assets": 9460613.54},
            {"asset_liability_ratio": 22.56, "liability_total_liabilities": 43673.77, "asset_total_assets": 193564.16},
        ]
        hint = _industry_mean_hint("计算结果是否符合“负债总额/资产总额”", rows)
        assert hint is not None
        assert "3 家" in hint
        assert "20.61" in hint

    def test_no_hint_when_period_rows_without_company(self):
        """带 report_year/report_period 列（单公司多期）且无公司标签时，不当作行业均值样本。"""
        rows = [
            {"report_year": 2023, "report_period": "Q3", "asset_liability_ratio": 40.0},
            {"report_year": 2024, "report_period": "Q3", "asset_liability_ratio": 41.0},
            {"report_year": 2025, "report_period": "Q3", "asset_liability_ratio": 42.0},
        ]
        assert _industry_mean_hint("计算三年的资产负债率均值", rows) is None

    def test_no_hint_when_not_mean_question(self):
        assert _industry_mean_hint("华润三九近三年营收走势", ROWS_73) is None

    def test_no_hint_for_single_row(self):
        assert _industry_mean_hint("计算行业资产负债率均值", [ROWS_73[0]]) is None

    def test_no_hint_when_field_missing(self):
        rows = [{"stock_abbr": "a", "roe": 10.0}, {"stock_abbr": "b", "roe": 12.0}]
        assert _industry_mean_hint("各中药企业研发费用情况", rows) is None


class TestMeanConsistency:
    """分析文本与均值口径的自洽性核验。"""

    def test_pass_when_value_matches(self):
        hint = _industry_mean_hint("行业资产负债率均值", ROWS_73)
        assert hint is not None
        text = "2025年第三季度行业资产负债率均值为 20.61%，按 3 家公司算术平均。"
        assert _mean_consistency_error("行业资产负债率均值", ROWS_73, text, hint) is None

    def test_fail_when_uses_first_row_value(self):
        hint = _industry_mean_hint("行业资产负债率均值", ROWS_73)
        text = "行业均值为 14.81%，由首家公司总负债 14.12 亿 / 总资产 95.33 亿得出。"
        err = _mean_consistency_error("行业资产负债率均值", ROWS_73, text, hint)
        assert err is not None
        assert "20.61" in err

    def test_pass_no_mean_claim(self):
        hint = _industry_mean_hint("行业资产负债率均值", ROWS_73)
        assert _mean_consistency_error("行业资产负债率均值", ROWS_73, "只说明单公司情况。", hint) is None

    def test_single_company_cannot_claim_industry_mean(self):
        rows = [{"stock_abbr": "广誉远", "asset_liability_ratio": 41.0}]
        text = "该公司的资产负债率为 41.00%，行业均值为 41.00%。"
        err = _mean_consistency_error("计算行业均值", rows, text, None)
        assert err is not None
        assert "仅含 1 家公司行" in err


class TestFormulaEquivalenceGuard:
    """“平均负债/平均资产相除=比率均值”错误等价句核验（B2036-Q2）。"""

    def test_detect_division_equivalence_claim(self):
        text = "经核算，平均负债总额为32.05亿元，平均资产总额为101.44亿元，两者相除结果与直接计算的比率均值一致，口径逻辑闭环。"
        err = _mean_ratio_equivalence_issue(text)
        assert err is not None
        assert "加权口径" in err

    def test_detect_reordered_claim(self):
        text = "把平均负债总额除以平均资产总额，得到的结果与行业比率均值相同。"
        assert _mean_ratio_equivalence_issue(text) is not None

    def test_pass_when_distinguishes_two_means(self):
        text = "行业均值为20.61%系算术平均；加权口径（总负债合计/总资产合计）约为21.34%，两者口径不同。"
        assert _mean_ratio_equivalence_issue(text) is None

    def test_pass_when_only_single_company_totals(self):
        text = "该公司的总负债为14.12亿元，总资产为95.33亿元，负债率14.81%。"
        assert _mean_ratio_equivalence_issue(text) is None

    def test_consistency_retry_when_false_equivalence(self):
        hint = _industry_mean_hint("计算2025年第三季度66家公司的资产负债率行业均值", ROWS_73)
        text = "2025年第三季度行业均值为20.61%。平均负债总额为32.05亿元，平均资产总额为101.44亿元，两者相除结果与比率均值一致。"
        err = _mean_consistency_error("计算结果是否符合“负债总额/资产总额”", ROWS_73, text, hint)
        assert err is not None
        assert "平均负债" in err
