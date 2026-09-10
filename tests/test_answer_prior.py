"""答案先验与「误拒答（应有数据）」判定离线单测（B-36）——零外部依赖。

不调用 LLM、不连 MySQL：标准 SQL 的只读执行一律通过注入的 stub executor 模拟，
真实数据库只读复算由运行期的人工验收（B-36 第四步）覆盖。
"""

import json
from pathlib import Path

import pytest

from eval.answer_keys import (
    DEFAULT_ANSWER_KEYS,
    EXPECT_HAS_DATA,
    EXPECT_NO_DATA,
    EXPECT_UNKNOWN,
    MISREFUSAL_TYPE,
    PRIOR_COLUMNS,
    STATUS_CONFLICT,
    STATUS_ERROR,
    STATUS_HAS_DATA,
    STATUS_NO_DATA,
    STATUS_UNKNOWN,
    AnswerKey,
    answer_gives_data,
    attach_priors,
    execute_readonly_sql,
    judge_misrefusal,
    load_answer_keys,
    looks_like_refusal,
    prior_number_hit_rate,
    resolve_prior,
)
from eval.llm_judge import DISAGREEMENT_COLUMNS, find_disagreements, summarize

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CONSISTENCY_RUNS = _REPO_ROOT / "训练结果数据" / "consistency_20260910" / "consistency_runs.json"

# B2053 真题问题与 B-25A 实测拒答原文（复述问题，故含 0.8 / 1.2 等数字）
_QUESTION = "计算2025年第三季度各公司的经营性现金流净额/净利润比值，找出比值在0.8-1.2之间的公司并统计数量"
_REFUSAL = (
    "抱歉，未查询到「计算2025年第三季度各上市公司的经营性现金流净额与净利润的比值，"
    "筛选比值在0.8到1.2之间的公司，并统计数量」的相关数据。当前数据库仅覆盖已入库上市公司的"
    "财报与研报，可能尚未收录该主体、期间或指标口径。 （SQL 已执行成功，但未返回任何数据） "
    "如需帮助，请换一个已覆盖范围的问题。"
)
_ANSWER_OK = "2025 年第三季度，共有 14 家上市公司的经营性现金流净额与净利润比值落在 0.8 至 1.2 的合理区间内。"

_STD_SQL = "SELECT 1 FROM t WHERE r BETWEEN 0.8 AND 1.2"


def _stub(rows):
    """构造只读执行器 stub：返回固定行数，记录调用次数。"""

    def _run(sql):
        _run.calls.append(sql)
        if isinstance(rows, str):
            return None, rows
        return list(rows), ""

    _run.calls = []
    return _run


# ---------------- 登记表加载 ----------------


def test_load_answer_keys_missing_file_returns_empty(tmp_path: Path):
    assert load_answer_keys(tmp_path / "nope.json") == {}


def test_load_answer_keys_broken_file_returns_empty(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_answer_keys(bad) == {}


def test_load_answer_keys_filters_draft_and_normalizes_expect(tmp_path: Path):
    f = tmp_path / "k.json"
    f.write_text(
        json.dumps(
            {
                "items": [
                    {"编号": "A1", "expect": EXPECT_HAS_DATA, "标准SQL": "SELECT 1", "审核": "approved"},
                    {"编号": "A2", "expect": EXPECT_NO_DATA, "审核": "draft"},
                    {"编号": "A3", "expect": "乱写", "审核": "approved"},
                    {"编号": "", "expect": EXPECT_NO_DATA, "审核": "approved"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    keys = load_answer_keys(f)
    assert set(keys) == {"A1", "A3"}
    assert keys["A3"].expect == EXPECT_UNKNOWN


def test_real_registry_covers_three_samples():
    """真实登记表（本机资产）须含 B2053 / B2004 / B2036 且状态符合口径。"""
    if not DEFAULT_ANSWER_KEYS.exists():
        pytest.skip("未找到先验登记表（本机数据资产，不入库）")
    keys = load_answer_keys()
    assert keys["B2053"].expect == EXPECT_HAS_DATA
    assert keys["B2053"].standard_sql.strip().upper().startswith("SELECT")
    assert keys["B2004"].expect == EXPECT_NO_DATA
    assert keys["B2036"].expect == EXPECT_NO_DATA


# ---------------- 只读执行器安全口径 ----------------


def test_execute_readonly_sql_blocks_non_select():
    rows, err = execute_readonly_sql("DELETE FROM balance_sheet")
    assert rows is None and "非 SELECT" in err


def test_execute_readonly_sql_empty_sql_is_noop():
    assert execute_readonly_sql("   ") == ([], "")


# ---------------- 先验解析三态 ----------------


def test_resolve_prior_no_data_never_runs_sql():
    run = _stub([{"a": 1}])
    out = resolve_prior(AnswerKey("B2036", EXPECT_NO_DATA, "SELECT 1"), run)
    assert out["status"] == STATUS_NO_DATA
    assert run.calls == []


def test_resolve_prior_has_data_recomputes_and_counts_rows():
    out = resolve_prior(AnswerKey("B2053", EXPECT_HAS_DATA, _STD_SQL), _stub([{"a": 1}] * 14))
    assert out["status"] == STATUS_HAS_DATA
    assert out["rows"] == 14


def test_resolve_prior_conflict_when_standard_sql_returns_zero():
    out = resolve_prior(AnswerKey("X", EXPECT_HAS_DATA, _STD_SQL), _stub([]))
    assert out["status"] == STATUS_CONFLICT
    assert "0 行" in out["reason"]


def test_resolve_prior_error_when_executor_fails():
    out = resolve_prior(AnswerKey("X", EXPECT_HAS_DATA, _STD_SQL), _stub("OperationalError: boom"))
    assert out["status"] == STATUS_ERROR


def test_resolve_prior_conflict_when_has_data_without_sql():
    out = resolve_prior(AnswerKey("X", EXPECT_HAS_DATA, "  "), _stub([{"a": 1}]))
    assert out["status"] == STATUS_CONFLICT


def test_resolve_prior_unknown_expect():
    assert resolve_prior(AnswerKey("X", EXPECT_UNKNOWN), _stub([{"a": 1}]))["status"] == STATUS_UNKNOWN


# ---------------- 判定规则 ----------------


def test_answer_gives_data_subtracts_question_numbers():
    """拒答模板复述问题，问题自带的 0.8/1.2 不得算作「已给数据」（假阳性护栏）。"""
    assert answer_gives_data(_REFUSAL) is True  # 不扣问题时会被误判
    assert answer_gives_data(_REFUSAL, _QUESTION) is False
    assert answer_gives_data(_ANSWER_OK, _QUESTION) is True


def test_looks_like_refusal_matches_misrefusal_wording():
    assert looks_like_refusal(_REFUSAL) is True
    assert looks_like_refusal(_ANSWER_OK) is False


@pytest.mark.parametrize("status", [STATUS_NO_DATA, STATUS_UNKNOWN, STATUS_CONFLICT, STATUS_ERROR])
def test_judge_misrefusal_never_fires_on_non_has_data(status: str):
    assert judge_misrefusal(status, _REFUSAL, _QUESTION) == ("", "")


def test_judge_misrefusal_fires_on_has_data_plus_refusal():
    flag, reason = judge_misrefusal(STATUS_HAS_DATA, _REFUSAL, _QUESTION)
    assert flag == MISREFUSAL_TYPE
    assert "应有数据" in reason


def test_judge_misrefusal_ignores_answer_that_gives_data():
    assert judge_misrefusal(STATUS_HAS_DATA, _ANSWER_OK, _QUESTION) == ("", "")
    # B2069 形态：含「无法」字样但给出了具体数值 → 不算误拒答
    hedged = "根据现有财务数据，无法确认该公司2025年第三季度投资性现金流量净额为正，本期为-2298.86万元。"
    assert answer_gives_data(hedged) is True
    assert judge_misrefusal(STATUS_HAS_DATA, hedged, "") == ("", "")


# ---------------- 明细附加列 ----------------


def test_attach_priors_adds_columns_and_resolves_once_per_code():
    run = _stub([{"a": 1}] * 14)
    rows = [{"编号": "B2053", "子问题": _QUESTION, "答案": _REFUSAL} for _ in range(3)]
    out = attach_priors(rows, {"B2053": AnswerKey("B2053", EXPECT_HAS_DATA, _STD_SQL)}, run)
    assert all(col in out[0] for col in PRIOR_COLUMNS)
    assert len(run.calls) == 1, "编号级先验只应复算一次"
    assert out[0]["误拒答判定"] == MISREFUSAL_TYPE
    assert out[0]["先验"] == STATUS_HAS_DATA
    assert rows[0].get("先验") is None, "不得原地修改入参"


def test_attach_priors_marks_unregistered_as_unknown():
    out = attach_priors([{"编号": "Z9", "子问题": "q", "答案": _REFUSAL}], {}, _stub([]))
    assert out[0]["先验"] == STATUS_UNKNOWN
    assert out[0]["误拒答判定"] == ""
    assert out[0]["本次SQL行数"] == "—"


def test_attach_priors_keeps_this_run_sql_row_count():
    rows = [{"编号": "Z9", "子问题": "q", "答案": _ANSWER_OK, "本次SQL行数": 0}]
    assert attach_priors(rows, {}, _stub([]))[0]["本次SQL行数"] == 0


def test_prior_number_hit_rate():
    assert prior_number_hit_rate("", [{"a": 1}]) is None
    assert prior_number_hit_rate(_ANSWER_OK, []) is None
    assert prior_number_hit_rate("营收 12.53 亿元", [{"v": "12.53"}]) == 1.0
    assert prior_number_hit_rate("营收 12.53 亿元", [{"v": "9.99"}]) == 0.0


# ---------------- 与分歧清单/汇总的衔接 ----------------


def test_find_disagreements_includes_misrefusal_and_prior_columns():
    rows = [
        {
            "编号": "B2053", "子问题": _QUESTION, "第几次": 2, "judge判定": "pass", "规则信号": "pass",
            "理由": "r", "先验": STATUS_HAS_DATA, "先验依据": "标准 SQL 只读复算 14 行",
            "误拒答判定": MISREFUSAL_TYPE, "先验理由": "先验显示该题应有数据，但本次回答命中拒答/澄清话术且未给出数值",
            "本次SQL行数": 0, "先验数值命中率": "—",
        }
    ]
    out = find_disagreements(rows)
    assert len(out) == 1
    assert MISREFUSAL_TYPE in out[0]["分歧类型"]
    assert set(out[0]) == set(DISAGREEMENT_COLUMNS)
    assert out[0]["先验"] == STATUS_HAS_DATA


def test_find_disagreements_without_prior_columns_still_works():
    rows = [{"编号": "Q1", "子问题": "q", "第几次": 1, "judge判定": "pass", "规则信号": "fail", "理由": "r"}]
    out = find_disagreements(rows)
    assert set(out[0]) == set(DISAGREEMENT_COLUMNS)
    assert out[0]["误拒答判定"] == ""


def test_summarize_reports_prior_stats():
    rows = [
        {"judge判定": "pass", "判据1": "pass", "判据2": "pass", "判据3": "pass", "判据4": "na",
         "规则信号": "pass", "先验": STATUS_HAS_DATA, "误拒答判定": MISREFUSAL_TYPE},
        {"judge判定": "pass", "判据1": "na", "判据2": "na", "判据3": "na", "判据4": "pass",
         "规则信号": "pass", "先验": STATUS_NO_DATA, "误拒答判定": ""},
    ]
    summary = summarize(rows, [])
    assert summary["误拒答（应有数据）条数"] == 1
    assert summary["先验分布"] == {STATUS_HAS_DATA: 1, STATUS_NO_DATA: 1}
    assert "确定性规则" in summary["先验口径声明"]


# ---------------- 反向验证（真实产物 + stub 标准 SQL 执行器） ----------------


def _runs_by_code() -> dict:
    payload = json.loads(_CONSISTENCY_RUNS.read_text(encoding="utf-8"))
    return {q["编号"]: q for q in payload["questions"]}


@pytest.mark.skipif(not _CONSISTENCY_RUNS.exists(), reason="缺少 B-25A 一致性套件产物")
def test_reverse_validation_no_data_samples_not_flagged():
    """B2004（开放题 5/5 合理拒答）与 B2036（B-30 守卫拒答）不得被判误拒答。"""
    if not DEFAULT_ANSWER_KEYS.exists():
        pytest.skip("未找到先验登记表（本机数据资产，不入库）")
    keys = load_answer_keys()
    runs = _runs_by_code()
    rows = []
    for code in ("B2004", "B2036"):
        for run in runs.get(code, {}).get("runs", []):
            rows.append({"编号": code, "子问题": runs[code]["子问题"], "答案": run.get("答案") or ""})
    assert rows, "未取到 B2004/B2036 的实测回答"
    out = attach_priors(rows, keys, _stub([{"a": 1}]))
    assert all(r["先验"] == STATUS_NO_DATA for r in out)
    assert [r for r in out if r["误拒答判定"]] == []


@pytest.mark.skipif(not _CONSISTENCY_RUNS.exists(), reason="缺少 B-25A 一致性套件产物")
def test_reverse_validation_b2053_misrefusal_flagged():
    """B2053 的 4 次误拒答须被识别（标准 SQL 用 stub 模拟「复算有数据」）。"""
    if not DEFAULT_ANSWER_KEYS.exists():
        pytest.skip("未找到先验登记表（本机数据资产，不入库）")
    keys = load_answer_keys()
    rec = _runs_by_code().get("B2053")
    if not rec:
        pytest.skip("B-25A 产物中无 B2053")
    rows = [
        {"编号": "B2053", "子问题": rec["子问题"], "答案": r.get("答案") or ""}
        for r in rec["runs"]
    ]
    out = attach_priors(rows, keys, _stub([{"a": 1}] * 14))
    flagged = [r for r in out if r["误拒答判定"] == MISREFUSAL_TYPE]
    assert len(flagged) == 4, f"应识别 4 次误拒答，实际 {len(flagged)}"
    assert out[0]["误拒答判定"] == "", "第 1 次给了 14 家，不得判误拒答"
# ---------------- judge_rows ↔ 先验的接线（回归护栏） ----------------


def test_judge_rows_feeds_answer_text_into_prior_check():
    """护栏：judge_rows 输出行只存字数，但先验判定必须拿到答案原文。"""
    from eval.llm_judge import judge_rows

    rows = [
        {"编号": "B2053", "第几次": 1, "子问题": _QUESTION, "答案": _ANSWER_OK, "SQL": "", "引用": []},
        {"编号": "B2053", "第几次": 2, "子问题": _QUESTION, "答案": _REFUSAL, "SQL": "", "引用": []},
    ]
    keys = {"B2053": AnswerKey("B2053", EXPECT_HAS_DATA, _STD_SQL)}
    out = judge_rows(
        rows, None, with_sql_result=False, answer_keys=keys, prior_executor=_stub([{"a": 1}] * 14)
    )
    assert out[0]["误拒答判定"] == "", "给了 14 家的那一次不得判误拒答"
    assert out[1]["误拒答判定"] == MISREFUSAL_TYPE
    assert out[0]["先验"] == STATUS_HAS_DATA
    assert "答案" not in out[0], "不得把答案原文写进判定产物"
    assert out[0]["答案字数"] == len(_ANSWER_OK)