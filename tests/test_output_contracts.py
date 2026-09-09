# -*- coding: utf-8 -*-
"""B-17 输出契约层单测（零外部依赖，mock 契约对象与 tmp 落盘）。"""

from __future__ import annotations

import json

import pytest

from utils.output_contracts import (
    ContractStats,
    validate_aggregate_result,
    validate_metric_plan,
    validate_sql_output,
    validate_supervisor_output,
)


# ── supervisor 拆解 ──────────────────────────────────────────────────
class TestSupervisorOutput:
    def test_valid_dict_tasks(self) -> None:
        res = validate_supervisor_output(
            {"tasks": [{"agent": "financial", "query": "查询净利润"}, {"agent": "research", "query": "研报观点"}]}
        )
        assert res.ok
        assert len(res.normalized) == 2

    def test_valid_list_tasks(self) -> None:
        res = validate_supervisor_output([{"agent": "research", "query": "近三年研发管线评价"}])
        assert res.ok

    def test_direct_answer_only(self) -> None:
        res = validate_supervisor_output({"direct_answer": "需要补充年份信息。"})
        assert res.ok

    def test_agent_not_in_whitelist(self) -> None:
        res = validate_supervisor_output({"tasks": [{"agent": "sql", "query": "x"}]})
        assert not res.ok
        assert any("白名单" in e for e in res.errors)

    def test_empty_query_rejected(self) -> None:
        res = validate_supervisor_output({"tasks": [{"agent": "financial", "query": "  "}]})
        assert not res.ok

    def test_empty_output_rejected(self) -> None:
        res = validate_supervisor_output({})
        assert not res.ok
        assert any("direct_answer" in e for e in res.errors)

    def test_non_json_value_rejected(self) -> None:
        res = validate_supervisor_output("不是 JSON")
        assert not res.ok


# ── aggregator 汇总 ──────────────────────────────────────────────────
class TestAggregateResult:
    def test_valid_result(self) -> None:
        res = validate_aggregate_result({"content": "净利润为 12.3 亿", "image": [], "references": []})
        assert res.ok

    def test_missing_content(self) -> None:
        res = validate_aggregate_result({"answer": "x"})
        assert not res.ok

    def test_empty_content(self) -> None:
        res = validate_aggregate_result({"content": "   "})
        assert not res.ok

    def test_non_dict_rejected(self) -> None:
        res = validate_aggregate_result(["content"])
        assert not res.ok

    def test_references_wrong_type(self) -> None:
        res = validate_aggregate_result({"content": "x", "references": "not-a-list"})
        assert not res.ok

    def test_chart_json_wrong_type(self) -> None:
        res = validate_aggregate_result({"content": "x", "chart_json": "bad"})
        assert not res.ok

    def test_chart_json_valid(self) -> None:
        res = validate_aggregate_result({"content": "x", "chart_json": {"type": "bar"}})
        assert res.ok


# ── metric 标准化 ────────────────────────────────────────────────────
def _valid_plan() -> dict:
    return {
        "standard_fields": ["net_profit"],
        "time_grain": {"mode": "single", "report_year": 2025, "report_period": "Q3"},
        "calculation": {"kind": "single"},
        "filter_terms": {"company": "片仔癀"},
    }


class TestMetricPlan:
    def test_valid_plan(self) -> None:
        assert validate_metric_plan(_valid_plan()).ok

    def test_missing_standard_fields(self) -> None:
        plan = _valid_plan()
        plan.pop("standard_fields")
        assert not validate_metric_plan(plan).ok

    def test_empty_standard_fields(self) -> None:
        plan = _valid_plan()
        plan["standard_fields"] = []
        assert not validate_metric_plan(plan).ok

    def test_non_str_field(self) -> None:
        plan = _valid_plan()
        plan["standard_fields"] = ["net_profit", 123]
        assert not validate_metric_plan(plan).ok

    def test_time_grain_missing_mode(self) -> None:
        plan = _valid_plan()
        plan["time_grain"] = {"report_year": 2025}
        assert not validate_metric_plan(plan).ok

    def test_calculation_missing_kind(self) -> None:
        plan = _valid_plan()
        plan["calculation"] = {"top_n": 5}
        assert not validate_metric_plan(plan).ok

    def test_non_dict_rejected(self) -> None:
        assert not validate_metric_plan("json-string").ok

    def test_filter_terms_wrong_type(self) -> None:
        plan = _valid_plan()
        plan["filter_terms"] = []
        assert not validate_metric_plan(plan).ok


# ── SQL 产物 ─────────────────────────────────────────────────────────
class TestSqlOutput:
    def test_valid_single_select(self) -> None:
        res = validate_sql_output("SELECT stock_abbr, net_profit FROM core_performance_indicators_sheet WHERE report_year = 2025;")
        assert res.ok

    def test_valid_multi_statements(self) -> None:
        sql = "SELECT AVG(x) AS a FROM core_performance_indicators_sheet WHERE report_year = 2025; SELECT SUM(y) FROM core_performance_indicators_sheet;"
        assert validate_sql_output(sql).ok

    def test_fence_and_sql_label_stripped(self) -> None:
        sql = "```sql\nSELECT 1 AS one\n```"
        assert validate_sql_output(sql).ok

    def test_insert_rejected(self) -> None:
        assert not validate_sql_output("INSERT INTO t VALUES (1);").ok

    def test_drop_rejected(self) -> None:
        res = validate_sql_output("DROP TABLE income_sheet;")
        assert not res.ok
        assert any("危险" in e for e in res.errors)

    def test_update_rejected(self) -> None:
        assert not validate_sql_output("UPDATE core_performance_indicators_sheet SET net_profit = 0;").ok

    def test_fullwidth_punctuation_rejected(self) -> None:
        res = validate_sql_output("SELECT stock_abbr，net_profit FROM income_sheet;")
        assert not res.ok
        assert any("全角" in e for e in res.errors)

    def test_empty_rejected(self) -> None:
        assert not validate_sql_output("").ok

    def test_too_many_statements_rejected(self) -> None:
        sql = ";".join(["SELECT 1 AS x FROM core_performance_indicators_sheet WHERE report_year = 2025"] * 21)
        assert not validate_sql_output(sql).ok


# ── ContractStats 落盘与计数 ─────────────────────────────────────────
class TestContractStats:
    def test_record_and_summary(self, tmp_path) -> None:
        stats = ContractStats(path=tmp_path / "events.jsonl", enabled=True)
        stats.record("metric_plan", True)
        stats.record("metric_plan", False, ["standard_fields 缺失"])
        stats.record("sql_output", True)
        s = stats.summary()
        assert s["metric_plan"]["calls"] == 2
        assert s["metric_plan"]["fail"] == 1
        assert s["metric_plan"]["format_error_rate"] == 0.5
        assert s["sql_output"]["calls"] == 1
        assert stats.path.read_text(encoding="utf-8").count("\n") == 3

    def test_record_disabled(self, tmp_path) -> None:
        stats = ContractStats(path=tmp_path / "events.jsonl", enabled=False)
        stats.record("metric_plan", False)
        assert not stats.path.exists()

    def test_error_truncated_to_three(self, tmp_path) -> None:
        stats = ContractStats(path=tmp_path / "events.jsonl", enabled=True)
        stats.record("sql_output", False, [f"err{i}" for i in range(6)])
        line = stats.path.read_text(encoding="utf-8").splitlines()[0]
        payload = json.loads(line)
        assert len(payload["errors"]) == 3
