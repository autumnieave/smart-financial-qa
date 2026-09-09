# -*- coding: utf-8 -*-
"""B-14 三层结构治理（financial 试点）单测：零外部依赖。

校验：build_financial_prompt 按任务类型组装的文本与公开常量一致；financial_layers 返回
战略/任务/细化三层且拼接还原完整文本（组装零文本变更，模型可见文本与 B-12 一致）；
未知任务类型抛 ValueError。
"""

from __future__ import annotations

import pytest

import prompts.financial as fin

_TASKS = {
    "metric_standardization": "METRIC_STANDARDIZATION_SYSTEM_PROMPT",
    "sql_gen": "SQL_GEN_SYSTEM_PROMPT",
    "analysis": "ANALYSIS_SYSTEM_PROMPT",
    "chart_gen": "CHART_GEN_SYSTEM_PROMPT",
}


def test_version_unchanged_from_b12() -> None:
    assert fin.FINANCIAL_PROMPT_VERSION == "2026-09-06-v8"


def test_build_equals_public_constants() -> None:
    for kind, const_name in _TASKS.items():
        assert fin.build_financial_prompt(kind) == getattr(fin, const_name), kind


def test_layers_cover_full_text_without_loss() -> None:
    for kind, const_name in _TASKS.items():
        layers = fin.financial_layers(kind)
        assert set(layers) == {"strategy", "task", "detail"}
        joined = layers["strategy"] + layers["task"] + layers["detail"]
        assert joined == getattr(fin, const_name), kind


def test_layers_non_empty_and_meaningful() -> None:
    for kind, const_name in _TASKS.items():
        layers = fin.financial_layers(kind)
        assert len(layers["strategy"]) > 20
        assert len(layers["task"]) > 20
        assert len(layers["detail"]) > 20


def test_strategy_carries_role_and_compliance() -> None:
    for kind in _TASKS:
        strategy = fin.financial_layers(kind)["strategy"]
        assert "你是一个" in strategy or "你是一位" in strategy, kind


def test_sql_detail_keeps_whitelist_doc_single_source() -> None:
    detail = fin.financial_layers("sql_gen")["detail"]
    assert "库内四张表字段白名单" in detail
    assert "core_performance_indicators_sheet" in detail
    # 白名单文档仍以单一常量 _FINANCIAL_FIELD_DOC 作为唯一来源
    assert fin._FINANCIAL_FIELD_DOC in detail


def test_metric_detail_keeps_field_mapping_few_shot() -> None:
    detail = fin.financial_layers("metric_standardization")["detail"]
    assert "资产负债率=asset_liability_ratio" in detail


def test_analysis_detail_keeps_output_examples() -> None:
    detail = fin.financial_layers("analysis")["detail"]
    assert "示例 A" in detail
    assert "示例 B" in detail
    assert "示例 C" in detail


def test_unknown_kind_raises() -> None:
    with pytest.raises(ValueError):
        fin.build_financial_prompt("not_a_task")
    with pytest.raises(ValueError):
        fin.financial_layers("not_a_task")


def test_fragments_slice_built_from_original_anchors() -> None:
    # 战略层片段应以原文段首为起点（防未来误删角色定义）
    metric = fin.financial_layers("metric_standardization")
    assert metric["strategy"].startswith("你是一个金融指标标准化器")
    sql = fin.financial_layers("sql_gen")
    assert sql["strategy"].startswith("你是一个 MySQL 查询语句拼装器")
    analysis = fin.financial_layers("analysis")
    assert analysis["strategy"].startswith("你是一位专业的财务数据助手")
    chart = fin.financial_layers("chart_gen")
    assert chart["strategy"].startswith("你是一个金融图表生成器")
