# -*- coding: utf-8 -*-
"""tests/test_native_financial_merge.py —— 多语句查询结果的“公司-指标”行合并纯函数单测。

覆盖 tools/native_financial.py 的 _company_time_key / _merge_company_rows：
用于修复排名名单语句与指标语句行序不一致导致的“公司-数值”错位（B2040 等）。
"""

from tools.native_financial import _company_time_key, _merge_company_rows


REVENUE_ROWS = [
    {"stock_abbr": "白云山", "total_operating_revenue": 6160599.38},
    {"stock_abbr": "药明康德", "total_operating_revenue": 3285671.65},
    {"stock_abbr": "云南白药", "total_operating_revenue": 3065421.42},
]
RATIO_ROWS = [
    {"stock_abbr": "云南白药", "asset_liability_ratio": 25.35, "gross_profit_margin": 30.05},
    {"stock_abbr": "药明康德", "asset_liability_ratio": 24.45, "gross_profit_margin": 46.63},
    {"stock_abbr": "白云山", "asset_liability_ratio": 51.92, "gross_profit_margin": 17.60},
]
MEAN_ROW = {"avg_asset_liability_ratio": 30.87, "avg_gross_profit_margin": 52.77}


class TestCompanyTimeKey:
    def test_company_only_key(self):
        assert _company_time_key({"stock_abbr": "白云山", "revenue": 1}) == ("c", "白云山")

    def test_full_time_key(self):
        key = _company_time_key({"stock_code": "600332", "report_year": 2025, "report_period": "Q3"})
        assert key == ("t", "600332", "2025", "Q3")

    def test_no_company_returns_none(self):
        assert _company_time_key({"avg_asset_liability_ratio": 30.87}) is None

    def test_partial_time_returns_none(self):
        assert _company_time_key({"stock_abbr": "白云山", "report_year": 2025}) is None


class TestMergeCompanyRows:
    def test_merge_ranking_and_metric_rows(self):
        merged = _merge_company_rows(REVENUE_ROWS + RATIO_ROWS + [MEAN_ROW])
        by_name = {r["stock_abbr"]: r for r in merged if r.get("stock_abbr")}
        assert by_name["白云山"]["asset_liability_ratio"] == 51.92
        assert by_name["白云山"]["gross_profit_margin"] == 17.60
        assert by_name["白云山"]["total_operating_revenue"] == 6160599.38
        assert by_name["药明康德"]["asset_liability_ratio"] == 24.45
        assert by_name["云南白药"]["total_operating_revenue"] == 3065421.42
        # 无公司键的聚合行原样保留
        assert any("avg_asset_liability_ratio" in r for r in merged)

    def test_merge_keeps_original_company_count(self):
        merged = _merge_company_rows(REVENUE_ROWS + RATIO_ROWS)
        abbrs = [r["stock_abbr"] for r in merged if r.get("stock_abbr")]
        assert abbrs == ["白云山", "药明康德", "云南白药"]

    def test_conflict_rows_not_merged(self):
        rows = [
            {"stock_abbr": "金花股份", "report_year": 2023, "report_period": "FY", "revenue": 1.0},
            {"stock_abbr": "金花股份", "report_year": 2024, "report_period": "FY", "revenue": 2.0},
        ]
        # 时间标签完整，本来就不同 key，互不合并
        assert len(_merge_company_rows(rows)) == 2
        # 同为“公司+不同 revenue”且无时间标签 → 保守不合并（防数据丢失）
        no_time = [
            {"stock_abbr": "金花股份", "revenue": 1.0},
            {"stock_abbr": "金花股份", "revenue": 2.0},
        ]
        assert len(_merge_company_rows(no_time)) == 2

    def test_empty_input(self):
        assert _merge_company_rows([]) == []

    def test_same_fields_no_conflict_merges(self):
        rows = [
            {"stock_abbr": "A", "revenue": 100},
            {"stock_abbr": "A", "asset_liability_ratio": 10.0},
        ]
        merged = _merge_company_rows(rows)
        assert len(merged) == 1
        assert merged[0]["revenue"] == 100
        assert merged[0]["asset_liability_ratio"] == 10.0
