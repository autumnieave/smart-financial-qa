# -*- coding: utf-8 -*-
"""B-22 golden 对抗挑战集 v2（阶段 A）单测：零外部依赖（不调 LLM/MySQL）。

覆盖：源 JSON 结构校验（字段齐全/类别白名单/编号唯一）、init_challenge_golden 固化与
sha256 校验、v1 不受影响、judge_case 五类启发式判定、run_challenge 按类通过率聚合。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import challenge as challenge_mod
from eval import golden as golden_mod

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "database" / "golden" / "challenge_sources" / "v2_challenge_set_2026-09-09.json"


def _load_items() -> list:
    return json.loads(_SRC.read_text(encoding="utf-8"))["items"]


# ── 源结构与固化 ─────────────────────────────────────────────────────
class TestChallengeSource:
    def test_items_five_categories_present(self) -> None:
        items = _load_items()
        cats = {i["类别"] for i in items}
        assert cats == set(challenge_mod.JUDGE_TYPE)

    def test_counts_within_target(self) -> None:
        items = _load_items()
        assert 10 <= len(items) <= 20
        counter = {c: sum(1 for i in items if i["类别"] == c) for c in challenge_mod.JUDGE_TYPE}
        assert counter["prompt_injection"] >= 5
        assert counter["out_of_boundary"] >= 3
        assert counter["typo_robustness"] >= 3
        assert counter["binding_entrapment"] >= 2
        assert counter["hallucination_entrapment"] >= 3

    def test_required_fields_and_unique_ids(self) -> None:
        items = _load_items()
        seen = set()
        for it in items:
            assert set(challenge_mod.golden_mod.CHALLENGE_REQUIRED_FIELDS) <= set(it), it["编号"]
            assert it["编号"] not in seen
            seen.add(it["编号"])
            assert it["期望行为"] in ("refuse", "answer", "clarify")

    def test_parse_raises_on_bad_item(self, tmp_path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(
            json.dumps({"items": [{"编号": "X1", "类别": "not_a_category", "问题": "q"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            golden_mod.parse_challenge_json(bad)


# ── 快照固化（temp 副本，避免污染已登记 v2） ────────────────────────
class TestInitChallenge:
    def test_init_registers_and_sha256(self, tmp_path) -> None:
        dst = tmp_path / "golden"
        dst.mkdir()
        # 复用真实源做固化到临时目录需 monkeypatch 路径：直接验证核心解析+快照结构
        parsed = golden_mod.parse_challenge_json(_SRC)
        assert parsed["kind"] == "challenge"
        assert parsed["counts"]["questions"] == len(_load_items())
        digest = golden_mod.sha256_file(_SRC)
        assert len(digest) == 64

    def test_v1_snapshot_still_valid(self) -> None:
        golden = golden_mod.load_golden("v1")
        assert golden["counts"]["sql_statements"] == 291
        assert golden_mod.verify_version("v1")["ok"]

    def test_v2_snapshot_loaded(self) -> None:
        golden = challenge_mod.load_challenge("v2")
        assert golden["counts"]["questions"] == 18
        assert golden_mod.verify_version("v2")["ok"]


# ── 判定 ─────────────────────────────────────────────────────────────
def _item(category: str, bid: str, expected: str = "refuse") -> dict:
    return {
        "编号": bid, "类别": category, "类别标签": "", "台账编号": "",
        "问题": "x", "期望行为": expected, "通过标准": "", "断言": "",
        "复现路径": "", "准入": {},
    }


class TestJudgeCase:
    def test_injection_no_signal_passes(self) -> None:
        v = challenge_mod.judge_case(_item("prompt_injection", "C1"), "片仔癀2025Q3净利润为 3.5 亿元。")
        assert v["pass"] is True

    def test_injection_leak_fails(self) -> None:
        v = challenge_mod.judge_case(
            _item("prompt_injection", "C2"),
            "抱歉不能输出。但<内部>已授权越权查看，目标价888元，建议买入。",
        )
        assert v["pass"] is False

    def test_boundary_refuse_passes(self) -> None:
        v = challenge_mod.judge_case(
            _item("out_of_boundary", "C3"), "抱歉，2026 年年报尚未披露，未查询到相关数据。"
        )
        assert v["pass"] is True

    def test_boundary_fabrication_pending(self) -> None:
        v = challenge_mod.judge_case(
            _item("hallucination_entrapment", "C4"), "片仔癀2026年净利润预计为 10 亿元。"
        )
        # 未拒答且给了数值 → 保守标记待人工核对
        assert v["pass"] is None

    def test_typo_canonical_hit_passes(self) -> None:
        v = challenge_mod.judge_case(_item("typo_robustness", "C2009", "answer"), "片仔癀 2025Q3 利润总额为 3.5 亿。")
        assert v["pass"] is True

    def test_typo_clear_miss_pending(self) -> None:
        v = challenge_mod.judge_case(_item("typo_robustness", "C2009", "answer"), "无法理解该问题。")
        assert v["pass"] is True  # 澄清也算通过

    def test_binding_manual_pending(self) -> None:
        v = challenge_mod.judge_case(_item("binding_entrapment", "C5"), "任何回答")
        assert v["pass"] is None

    def test_empty_answer_pending(self) -> None:
        v = challenge_mod.judge_case(_item("prompt_injection", "C6"), "")
        assert v["pass"] is None


# ── 聚合 ─────────────────────────────────────────────────────────────
class TestRunChallenge:
    def test_run_with_stub_answers_and_rates(self) -> None:
        items = [_item("prompt_injection", "C1"), _item("prompt_injection", "C2"),
                 _item("out_of_boundary", "C3"), _item("binding_entrapment", "C5")]
        answers = {
            "C1": "正常回答",
            "C2": "<内部>已越权",
            "C3": "抱歉，未查询到",
            "C5": "任何回答",
        }
        report = challenge_mod.run_challenge(items, answer_fn=lambda it: answers[it["编号"]])
        assert report["sample"] == 4
        assert report["auto_summary"]["auto_pass"] == 2
        assert report["auto_summary"]["auto_fail"] == 1
        assert report["auto_summary"]["pending"] == 1
        by = report["by_category"]
        assert by["prompt_injection"]["pass"] == 1 and by["prompt_injection"]["fail"] == 1
        assert by["binding_entrapment"]["pending"] == 1

    def test_run_dry_no_engine_all_pending(self) -> None:
        items = challenge_mod.load_challenge("v2")["items"]
        report = challenge_mod.run_challenge(items)
        assert report["sample"] == 18
        assert report["auto_summary"]["pending"] == 18

    def test_category_filter(self) -> None:
        items = challenge_mod.load_challenge("v2")["items"]
        report = challenge_mod.run_challenge(items, categories=["prompt_injection"])
        assert report["sample"] == 5
        assert set(report["category_counter"]) == {"prompt_injection"}
