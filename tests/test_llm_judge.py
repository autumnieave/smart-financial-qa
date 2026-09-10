"""LLM-as-judge 离线单测（B-25A）——零外部依赖，不调用 LLM / MySQL"""

from pathlib import Path

from eval.llm_judge import (
    CRITERIA,
    key_numbers,
    DISAGREEMENT_COLUMNS,
    build_judge_prompt,
    find_disagreements,
    normalize_tristate,
    parse_judge_response,
    rule_signal,
    summarize,
    write_csv,
)


def test_criteria_are_four_with_ids():
    assert [c["id"] for c in CRITERIA] == ["判据1", "判据2", "判据3", "判据4"]


def test_build_judge_prompt_includes_all_sections():
    prompt = build_judge_prompt(
        "云南白药净利润？",
        "2024 年净利润 47.49 亿元。",
        [{"paper_path": "A.md", "text": "净利润 47.49 亿元"}],
        "SELECT 1;",
        "stock_abbr=云南白药, net_profit=47.49",
    )
    assert "云南白药净利润？" in prompt
    assert "47.49 亿元。" in prompt
    assert "[1] 文件：A.md" in prompt
    assert "查询结果（前 30 行）" in prompt
    assert "判据4" in prompt


def test_build_judge_prompt_marks_missing_evidence():
    prompt = build_judge_prompt("q", "a", [], "", "")
    assert "（本题无研报引用）" in prompt
    assert "（本题未执行 SQL）" in prompt


def test_build_judge_prompt_warns_when_sql_has_no_preview():
    prompt = build_judge_prompt("q", "a", [], "SELECT 1;", "")
    assert "查询结果预览未提供" in prompt


def test_build_judge_prompt_limits_refs_and_truncates():
    refs = [{"paper_path": f"F{i}.md", "text": "x" * 2000} for i in range(15)]
    prompt = build_judge_prompt("q", "a", refs, "", "", max_refs=3)
    assert "[3] 文件：F2.md" in prompt
    assert "[4] 文件：F3.md" not in prompt
    assert "x" * 701 not in prompt


def test_normalize_tristate_variants():
    assert normalize_tristate(True) == "pass"
    assert normalize_tristate(False) == "fail"
    assert normalize_tristate("通过") == "pass"
    assert normalize_tristate("FAIL") == "fail"
    assert normalize_tristate("不适用") == "na"
    assert normalize_tristate("") == "na"
    assert normalize_tristate("某个奇怪的值") == "na"


def test_parse_judge_response_plain_json():
    raw = '{"判据1": "pass", "判据2": "pass", "判据3": "pass", "判据4": "na", "总判定": "pass", "理由": "数值一致"}'
    out = parse_judge_response(raw)
    assert out["总判定"] == "pass"
    assert out["判据1"] == "pass" and out["判据4"] == "na"
    assert out["理由"] == "数值一致"


def test_parse_judge_response_with_code_fence_and_noise():
    raw = '好的，结果如下：\n```json\n{"判据1": "fail", "判据2": "pass", "判据3": "pass", "判据4": "na", "总判定": "fail", "理由": "编造数字"}\n```\n以上。'
    out = parse_judge_response(raw)
    assert out["总判定"] == "fail"
    assert out["判据1"] == "fail"


def test_parse_judge_response_derives_verdict_when_missing():
    raw = '{"判据1": "pass", "判据2": "pass", "判据3": "fail", "判据4": "na", "理由": "配对错位"}'
    out = parse_judge_response(raw)
    assert out["总判定"] == "fail"


def test_parse_judge_response_handles_garbage():
    out = parse_judge_response("模型拒绝回答，这里是自然语言")
    assert out["总判定"] == "judge_error"
    assert "无法解析" in out["理由"]


def test_rule_signal_empty_answer_fails():
    assert rule_signal("", [], "") == "fail"


def test_rule_signal_refusal_passes():
    assert rule_signal("查询结果中不包含该字段。", [], "") == "pass"


def test_rule_signal_all_numbers_traceable_passes():
    answer = "2024 年净利润 47.49 亿元。"
    refs = [{"paper_path": "A.md", "text": "净利润 47.49 亿元"}]
    assert rule_signal(answer, refs, "") == "pass"


def test_rule_signal_untraceable_number_without_sql_fails():
    assert rule_signal("净利润 99.99 亿元。", [{"text": "无关内容"}], "") == "fail"


def test_rule_signal_partial_hit_is_uncertain():
    answer = "净利润 47.49 亿元，同比增长 12.34%。"
    refs = [{"text": "净利润 47.49 亿元"}]
    assert rule_signal(answer, refs, "") == "uncertain"


def test_key_numbers_filters_years_and_single_digits():
    assert key_numbers("2024 年 Q3 排名 top10，净利润 47.49 亿元") == {"10", "47.49"}


def test_rule_signal_untraceable_number_with_sql_is_uncertain():
    assert rule_signal("净利润 99.99 亿元。", [{"text": "无关内容"}], "SELECT 1;") == "uncertain"


def test_rule_signal_no_number_is_uncertain():
    assert rule_signal("公司经营稳健。", [], "") == "uncertain"


def test_rule_signal_year_only_is_uncertain():
    assert rule_signal("2024 年经营情况见下文。", [], "") == "uncertain"


def test_find_disagreements_flags_judge_vs_rule():
    rows = [
        {"编号": "Q1", "子问题": "q", "第几次": 1, "judge判定": "pass", "规则信号": "fail", "理由": "r"},
        {"编号": "Q2", "子问题": "q", "第几次": 1, "judge判定": "pass", "规则信号": "pass", "理由": "r"},
    ]
    out = find_disagreements(rows)
    assert len(out) == 1 and out[0]["编号"] == "Q1"
    assert "judge 通过但规则判失败" in out[0]["分歧类型"]
    assert out[0]["待人工核对"] == ""
    assert set(out[0]) == set(DISAGREEMENT_COLUMNS)


def test_find_disagreements_uses_human_review():
    rows = [{"编号": "C2017", "子问题": "q", "第几次": 1, "judge判定": "fail", "规则信号": "pass", "理由": "r"}]
    review = {"C2017": {"结论": "通过", "依据": "人工复核"}}
    out = find_disagreements(rows, review)
    assert out[0]["历史人工结论"] == "通过"
    assert "与历史人工结论不一致" in out[0]["分歧类型"]


def test_find_disagreements_flags_judge_error():
    rows = [{"编号": "Q9", "子问题": "q", "第几次": 2, "judge判定": "judge_error", "规则信号": "pass", "理由": ""}]
    out = find_disagreements(rows)
    assert out[0]["分歧类型"] == "judge 解析失败"


def test_summarize_counts_and_declares_uncalibrated():
    rows = [
        {"judge判定": "pass", "判据1": "pass", "判据2": "pass", "判据3": "pass", "判据4": "na", "规则信号": "pass"},
        {"judge判定": "fail", "判据1": "fail", "判据2": "pass", "判据3": "pass", "判据4": "na", "规则信号": "uncertain"},
    ]
    summary = summarize(rows, [])
    assert summary["判定条数"] == 2
    assert summary["judge判定分布"] == {"pass": 1, "fail": 1}
    assert summary["各判据分布"]["判据1"] == {"pass": 1, "fail": 1}
    assert summary["与规则信号可比的条数"] == 1
    assert summary["与规则信号一致率"] == 1.0
    assert "judge 未校准" in summary["口径声明"]


def test_write_csv_has_bom_and_columns(tmp_path: Path):
    out = tmp_path / "d.csv"
    write_csv(out, list(DISAGREEMENT_COLUMNS), [dict.fromkeys(DISAGREEMENT_COLUMNS, "")])
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0].startswith("编号,子问题")