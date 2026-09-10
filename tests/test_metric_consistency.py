# -*- coding: utf-8 -*-
"""B-30 指标-问题一致性校验纯逻辑单测：零外部依赖（不调 LLM/MySQL）。

覆盖：SQL 取用指标计划外字段（近似字段替代）被拦截、标签列/函数/表达式/星号不误伤、
order_by 分子分母与 threshold 字段不误伤、多语句逐条判定，以及失败出口路由到
refuse.metric_mismatch 模板（prompts/fallback.py）。
"""

from __future__ import annotations

from prompts.fallback import (
    REFUSE_METRIC_MISMATCH,
    build_refuse_metric_mismatch,
    template_category,
)
from tools.native_financial import (
    _METRIC_MISMATCH_MARKER,
    _allowed_metric_fields,
    _sql_failure_reply,
    metric_field_consistency_error,
)

_PLAN = {
    "standard_fields": ["equity_unappropriated_profit", "stock_abbr", "report_year", "report_period"],
    "time_grain": {"mode": "single", "report_year": 2024, "report_period": "FY"},
    "calculation": {"kind": "raw"},
    "filter_terms": {"company": "云南白药", "companies": None, "scope": "named", "threshold": None},
}


def test_substituted_field_is_rejected() -> None:
    """C2016 场景：问未分配利润/公积金，SQL 却取 net_asset_per_share → 拦截。"""
    sql = (
        "SELECT stock_abbr, net_asset_per_share, report_year, report_period "
        "FROM core_performance_indicators_sheet WHERE stock_abbr LIKE '%云南白药%';"
    )
    err = metric_field_consistency_error(sql, _PLAN)
    assert err and _METRIC_MISMATCH_MARKER in err
    assert "net_asset_per_share" in err


def test_plan_fields_and_tags_pass() -> None:
    """仅使用计划内字段 + 标签列 → 不触发。"""
    sql = (
        "SELECT stock_abbr, equity_unappropriated_profit, report_year, report_period "
        "FROM balance_sheet WHERE stock_abbr LIKE '%云南白药%' AND report_year = 2024;"
    )
    assert metric_field_consistency_error(sql, _PLAN) is None


def test_functions_expressions_star_are_skipped() -> None:
    """函数 / 表达式 / 星号无法判定 → 跳过，避免误伤聚合与计算列。"""
    assert metric_field_consistency_error("SELECT AVG(asset_liability_ratio) FROM balance_sheet;", _PLAN) is None
    assert metric_field_consistency_error("SELECT * FROM balance_sheet LIMIT 5;", _PLAN) is None
    assert (
        metric_field_consistency_error(
            "SELECT stock_abbr, equity_unappropriated_profit / 10000 AS wan FROM balance_sheet;", _PLAN
        )
        is None
    )


def test_order_by_and_threshold_fields_not_flagged() -> None:
    """rank 的分子/分母 order_by 与 threshold 字段属于计划内原料字段 → 不误伤。"""
    plan = {
        "standard_fields": ["operating_expense_rnd_expenses"],
        "time_grain": {"mode": "none"},
        "calculation": {"kind": "rank", "order_by": "operating_expense_rnd_expenses/total_operating_revenue", "top_n": 5},
        "filter_terms": {"threshold": {"field": "total_operating_revenue", "op": ">=", "value": 2000000}},
    }
    sql = (
        "SELECT stock_abbr, operating_expense_rnd_expenses, total_operating_revenue, report_year, report_period "
        "FROM income_sheet t1 WHERE t1.total_operating_revenue >= 2000000 "
        "ORDER BY (t1.operating_expense_rnd_expenses / t1.total_operating_revenue) DESC LIMIT 5;"
    )
    assert "total_operating_revenue" in _allowed_metric_fields(plan)
    assert metric_field_consistency_error(sql, plan) is None


def test_none_plan_or_empty_fields_skips_check() -> None:
    """无计划（回退自选路径）或未标注标准字段 → 不校验，保持旧行为。"""
    assert metric_field_consistency_error("SELECT net_asset_per_share FROM balance_sheet;", None) is None
    assert (
        metric_field_consistency_error(
            "SELECT net_asset_per_share FROM balance_sheet;", {"standard_fields": [], "calculation": {"kind": "raw"}}
        )
        is None
    )


def test_multi_statement_checked_per_statement() -> None:
    """多语句逐条判定：任一语句越界即拦截。"""
    sql = (
        "SELECT stock_abbr, equity_unappropriated_profit FROM balance_sheet WHERE report_year = 2023;\n"
        "SELECT stock_abbr, net_asset_per_share FROM balance_sheet WHERE report_year = 2024;"
    )
    err = metric_field_consistency_error(sql, _PLAN)
    assert err and "net_asset_per_share" in err


def test_sql_failure_reply_routes_to_metric_mismatch() -> None:
    """一致性校验失败出口 → refuse.metric_mismatch（不给技术细节、不给近似口径数值）。"""
    content, template_id = _sql_failure_reply([f"{_METRIC_MISMATCH_MARKER}（B-30）：SQL 取用了指标计划外的字段 net_asset_per_share"])
    assert template_id == REFUSE_METRIC_MISMATCH
    assert "口径" in content
    assert "net_asset_per_share" not in content


def test_metric_mismatch_template_registered_as_refuse() -> None:
    """模板登记在 refuse 类，且话术给出可查指标示例。"""
    content, template_id = build_refuse_metric_mismatch(["每股公积金"])
    assert template_id == REFUSE_METRIC_MISMATCH
    assert template_category(template_id) == "refuse"
    assert "每股公积金" in content
    assert "营业收入" in content


# ── B-30 库外指标漏标兜底（词表） ────────────────────────────────────────


def test_unmarked_out_of_scope_detects_missed_marking() -> None:
    """模型把库外指标硬映射成近似字段（plan 未标注）→ 词表兜底命中。"""
    plan = {
        "standard_fields": ["net_asset_per_share", "stock_abbr"],
        "time_grain": {"mode": "none"},
        "calculation": {"kind": "raw"},
        "filter_terms": {},
    }
    from tools.native_financial import unmarked_out_of_scope_terms

    assert unmarked_out_of_scope_terms("片仔癀2025年三季度的每股公积金是多少？", plan) == ["每股公积金"]


def test_unmarked_out_of_scope_skips_already_marked() -> None:
    """已由 unsupported_metrics 标注的库外指标不重复触发（避免两条出口重复处置）。"""
    from tools.native_financial import unmarked_out_of_scope_terms

    plan = {"standard_fields": [], "unsupported_metrics": ["股价", "总市值"], "calculation": {"kind": "raw"}}
    assert unmarked_out_of_scope_terms("云南白药目前的股价与总市值是多少？", plan) == []
    plan2 = {"standard_fields": [], "unsupported_metrics": ["抖音电商 GMV"], "calculation": {"kind": "raw"}}
    assert unmarked_out_of_scope_terms("白云山2025年三季度的抖音电商 GMV 是多少？", plan2) == []


def test_unmarked_out_of_scope_ignores_normal_and_none_plan() -> None:
    """库内指标题与无 plan（回退自选路径）都不触发，保持旧行为。"""
    from tools.native_financial import unmarked_out_of_scope_terms

    plan = {"standard_fields": ["total_operating_revenue"], "calculation": {"kind": "raw"}}
    assert unmarked_out_of_scope_terms("云南白药2024年的营业收入和净利润是多少？", plan) == []
    assert unmarked_out_of_scope_terms("云南白药目前的股价是多少？", None) == []
    assert unmarked_out_of_scope_terms("云南白药目前的股价是多少？", {"standard_fields": [], "calculation": {}}) == [
        "股价"
    ]
