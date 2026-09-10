# -*- coding: utf-8 -*-
"""B-18 兜底话术模板单测：零外部依赖（不调 LLM/MySQL）。

覆盖三类模板（refuse / suggest / human）的文案与 template_id 固定性、类别映射、
投资建议关键词检测（tools/native_financial._advice_risk_type）与 log_fallback 事件通道。
"""

from __future__ import annotations

import json

from prompts.fallback import (
    FALLBACK_PROMPT_VERSION,
    HUMAN_HIGH_RISK_ADVICE,
    REFUSE_DATA_NOT_FOUND,
    REFUSE_METRIC_OUT_OF_SCOPE,
    REFUSE_NOT_UNDERSTOOD,
    REFUSE_OUT_OF_SCOPE,
    REFUSE_SYSTEM_UNAVAILABLE,
    REFUSE_YEAR_OUT_OF_RANGE,
    SUGGEST_MISSING_FIELD,
    build_human_risk_advice,
    build_refuse_data_not_found,
    build_refuse_metric_out_of_scope,
    build_refuse_not_understood,
    build_refuse_out_of_scope,
    build_refuse_system_unavailable,
    build_refuse_year_out_of_range,
    build_suggest_missing,
    log_fallback,
    template_category,
)
from tools.native_financial import _advice_risk_type
from utils.output_contracts import ContractStats


def test_module_version_format() -> None:
    assert FALLBACK_PROMPT_VERSION.count("-") == 3
    assert FALLBACK_PROMPT_VERSION.startswith("2026-")


def test_category_mapping() -> None:
    assert template_category(REFUSE_OUT_OF_SCOPE) == "refuse"
    assert template_category(REFUSE_DATA_NOT_FOUND) == "refuse"
    assert template_category(REFUSE_YEAR_OUT_OF_RANGE) == "refuse"
    assert template_category(REFUSE_SYSTEM_UNAVAILABLE) == "refuse"
    assert template_category(REFUSE_NOT_UNDERSTOOD) == "refuse"
    assert template_category(SUGGEST_MISSING_FIELD) == "suggest"
    assert template_category(HUMAN_HIGH_RISK_ADVICE) == "human"
    assert template_category("unknown.x") == "unknown"


class TestRefuseTemplates:
    def test_data_not_found_mentions_subject_and_reason(self) -> None:
        content, tid = build_refuse_data_not_found(subject="贵州茅台 2026Q1 净利润", detail="SQL 已执行但无数据")
        assert tid == REFUSE_DATA_NOT_FOUND
        assert "贵州茅台 2026Q1 净利润" in content
        assert "未查询到" in content
        assert "SQL 已执行但无数据" in content

    def test_year_out_of_range_mentions_latest(self) -> None:
        content, tid = build_refuse_year_out_of_range(subject="2026 年年报", year=2026, latest="2025Q3")
        assert tid == REFUSE_YEAR_OUT_OF_RANGE
        assert "2026 年" in content
        assert "2025Q3" in content

    def test_system_unavailable_with_reason(self) -> None:
        content, tid = build_refuse_system_unavailable(detail="MySQL 连接失败")
        assert tid == REFUSE_SYSTEM_UNAVAILABLE
        assert "MySQL 连接失败" in content

    def test_out_of_scope_refuses(self) -> None:
        content, tid = build_refuse_out_of_scope()
        assert tid == REFUSE_OUT_OF_SCOPE
        assert "不在我的回答范围内" in content

    def test_not_understood(self) -> None:
        content, tid = build_refuse_not_understood()
        assert tid == REFUSE_NOT_UNDERSTOOD
        assert "无法理解" in content


class TestSuggestTemplate:
    def test_with_clarify_question_keeps_existing_wording(self) -> None:
        content, tid = build_suggest_missing(["stock_name"], clarify_question="您想查询哪家公司的数据呢？")
        assert tid == SUGGEST_MISSING_FIELD
        assert content == "🤔 我需要一些额外信息来更准确地回答您的问题：您想查询哪家公司的数据呢？"

    def test_without_clarify_question_lists_fields(self) -> None:
        content, tid = build_suggest_missing(["公司名称", "时间期间"])
        assert tid == SUGGEST_MISSING_FIELD
        assert "公司名称" in content and "时间期间" in content


class TestHumanTemplate:
    def test_risk_advice_footer(self) -> None:
        content, tid = build_human_risk_advice(risk_type="投资建议")
        assert tid == HUMAN_HIGH_RISK_ADVICE
        assert "不构成任何投资建议" in content
        assert "人工复核" in content


class TestAdviceDetector:
    def test_advice_keyword_detected(self) -> None:
        assert _advice_risk_type("该股基本面稳健，建议买入并持有。") == "投资建议"

    def test_factual_text_no_risk(self) -> None:
        assert _advice_risk_type("2025 年 Q3 净利润为 3533.59 万元，同比改善。") is None
        assert _advice_risk_type("") is None
        assert _advice_risk_type(None) is None


class TestFallbackLogging:
    def test_log_fallback_records_event(self, tmp_path) -> None:
        stats = ContractStats(path=tmp_path / "events.jsonl", enabled=True)
        log_fallback(REFUSE_DATA_NOT_FOUND, detail="sql_ok_rows_empty", stats=stats)
        log_fallback(SUGGEST_MISSING_FIELD, detail="clarify_missing_stock_name", stats=stats)
        lines = stats.path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        payload = json.loads(lines[0])
        assert payload["kind"] == "fallback"
        assert payload["template_id"] == REFUSE_DATA_NOT_FOUND
        assert payload["category"] == "refuse"
        assert payload["detail"] == "sql_ok_rows_empty"
        assert stats.fallback_summary()[REFUSE_DATA_NOT_FOUND] == 1
        assert stats.fallback_summary()[SUGGEST_MISSING_FIELD] == 1

    def test_log_fallback_disabled_no_file(self, tmp_path) -> None:
        stats = ContractStats(path=tmp_path / "off.jsonl", enabled=False)
        log_fallback(REFUSE_NOT_UNDERSTOOD, stats=stats)
        assert not stats.path.exists()


# ── B-31 库外指标拒答 ───────────────────────────────────────────────────


def test_metric_out_of_scope_template_mentions_metrics_and_examples() -> None:
    content, tid = build_refuse_metric_out_of_scope(["股价", "总市值"])
    assert tid == REFUSE_METRIC_OUT_OF_SCOPE
    assert template_category(tid) == "refuse"
    assert "股价、总市值" in content
    assert "营业收入" in content  # 给出可查指标示例
    assert "不在覆盖范围内" in content


def test_metric_out_of_scope_template_without_names() -> None:
    content, tid = build_refuse_metric_out_of_scope(None)
    assert tid == REFUSE_METRIC_OUT_OF_SCOPE
    assert "该指标" in content


def test_sql_failure_reply_routes_field_errors_to_out_of_scope() -> None:
    """字段/白名单类失败 → 库外指标话术；格式类失败 → 不含技术堆栈的系统提示。"""
    from tools.native_financial import _sql_failure_reply

    content, tid = _sql_failure_reply(["格式契约拒绝: 语句首关键字非法: 无可用字段生成"])
    assert tid == REFUSE_METRIC_OUT_OF_SCOPE

    content2, tid2 = _sql_failure_reply(["格式契约拒绝: 包含全角标点（MySQL 不识别）"])
    assert tid2 == REFUSE_SYSTEM_UNAVAILABLE
    assert "全角" not in content2 and "契约" not in content2
