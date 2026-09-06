# -*- coding: utf-8 -*-
"""B-12 指标标准化（Text-to-SQL 两步分解第 1 步）纯逻辑单测：零外部依赖（不调 LLM/MySQL）。

覆盖：JSON 容错解析、plan 规整校验、user content 兜底文案、配置开关关闭时的离线短路，
以及 prompts/financial.py 新增提示词常量自检。
"""

from __future__ import annotations

import json

from config.rag_config import RAGConfig
from prompts import financial as financial_prompts
from tools.native_financial import (
    _METRIC_STANDARDIZE_MAX_TOKENS,
    _metric_plan_to_text,
    _normalize_metric_plan,
    _parse_metric_json_text,
    _standardize_metrics,
)

_SAMPLE_PLAN = {
    "standard_fields": ["asset_liability_ratio", "stock_abbr", "report_year", "report_period"],
    "time_grain": {"mode": "single", "report_year": 2025, "report_period": "Q3"},
    "calculation": {"kind": "industry_mean"},
    "filter_terms": {"company": None, "companies": None, "scope": "all", "threshold": None},
}


class _StubRag:
    """离线桩：仅暴露 config 属性，不发起任何网络调用。"""

    def __init__(self, config):
        self.config = config


class _StubConfig:
    def __init__(self, standardize=True):
        self.AGENT_METRIC_STANDARDIZE = standardize
        self.LLM_MODEL = "stub-model"


def test_metric_plan_to_text_none_uses_legacy_fallback_text():
    assert "无上游指标提取" in _metric_plan_to_text(None)


def test_metric_plan_to_text_serializes_json():
    text = _metric_plan_to_text(_SAMPLE_PLAN)
    assert '"standard_fields"' in text
    assert "industry_mean" in text
    parsed = json.loads(text)
    assert parsed["time_grain"]["report_period"] == "Q3"


def test_parse_metric_json_text_clean():
    plan = _parse_metric_json_text(json.dumps(_SAMPLE_PLAN, ensure_ascii=False))
    assert plan is not None
    assert plan["calculation"]["kind"] == "industry_mean"


def test_parse_metric_json_text_markdown_fence():
    plan = _parse_metric_json_text("```json\n" + json.dumps(_SAMPLE_PLAN, ensure_ascii=False) + "\n```")
    assert plan is not None
    assert plan["standard_fields"][0] == "asset_liability_ratio"


def test_parse_metric_json_text_preamble_and_trailing_comma():
    raw = '这是标准化结果：{"standard_fields": ["roe", "stock_abbr"], "time_grain": {"mode": "single", "report_year": 2025, "report_period": "Q3"}, "calculation": {"kind": "raw"}, "filter_terms": {},}'
    plan = _parse_metric_json_text(raw)
    assert plan is not None
    assert plan["calculation"]["kind"] == "raw"


def test_parse_metric_json_text_garbage_returns_none():
    assert _parse_metric_json_text("抱歉，无法生成。") is None
    assert _parse_metric_json_text("") is None


def test_normalize_metric_plan_valid_dedupes_fields():
    plan = {
        "standard_fields": ["roe", "roe", "stock_abbr"],
        "time_grain": {"mode": "single", "report_year": 2024, "report_period": "FY"},
        "calculation": {"kind": "raw"},
        "filter_terms": {"company": "广誉远", "scope": "named"},
    }
    normalized = _normalize_metric_plan(plan)
    assert normalized is not None
    assert normalized["standard_fields"] == ["roe", "stock_abbr"]


def test_normalize_metric_plan_rejects_missing_keys():
    assert _normalize_metric_plan(None) is None
    assert _normalize_metric_plan({"standard_fields": []}) is None
    assert _normalize_metric_plan({"standard_fields": ["roe"]}) is None
    assert (
        _normalize_metric_plan(
            {
                "standard_fields": ["roe"],
                "time_grain": {"mode": "single"},
                "calculation": {"kind": ""},
                "filter_terms": None,
            }
        )
        is None
    )
    # filter_terms 缺失/非 dict 时补默认空字典
    plan = _normalize_metric_plan(
        {
            "standard_fields": ["roe"],
            "time_grain": {"mode": "single", "report_year": 2024, "report_period": "FY"},
            "calculation": {"kind": "raw"},
        }
    )
    assert plan is not None
    assert plan["filter_terms"] == {}


def test_standardize_metrics_disabled_short_circuits_offline():
    rag = _StubRag(_StubConfig(standardize=False))
    # 关闭开关时不应触碰 client，直接返回 None（可离线验证）
    assert _standardize_metrics(rag, "2024年利润总额多少") is None


def test_metric_standardize_max_tokens_tightened():
    assert isinstance(_METRIC_STANDARDIZE_MAX_TOKENS, int)
    assert _METRIC_STANDARDIZE_MAX_TOKENS <= 500


def test_financial_prompts_new_constants_self_check():
    assert financial_prompts.FINANCIAL_PROMPT_VERSION == "2026-09-06-v8"
    metric = financial_prompts.METRIC_STANDARDIZATION_SYSTEM_PROMPT
    sql_gen = financial_prompts.SQL_GEN_SYSTEM_PROMPT
    assert "standard_fields" in metric
    assert "time_grain" in metric
    assert "calculation" in metric
    assert "filter_terms" in metric
    # SQL 侧收敛为“映射+拼装”并引用标准化 JSON
    assert "指标标准化结果" in sql_gen
    assert "字段白名单" in sql_gen


def test_config_default_metric_standardize_on():
    config = RAGConfig()
    assert getattr(config, "AGENT_METRIC_STANDARDIZE", True) is True
