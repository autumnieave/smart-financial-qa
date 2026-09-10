"""hallucination_audit 离线单测（B-23A）

零外部依赖：不连 Qdrant / MySQL / LLM；引用存在性用 stub validator + tmp_path 文件验证。
"""

from pathlib import Path

import pytest

from tools.data_scripts.hallucination_audit import (
    CHECKLIST_COLUMNS,
    audit_one_reference,
    build_checklist,
    classify_number,
    collapse_number_spaces,
    iter_number_items,
    iter_number_items_with_lines,
    match_hit_refs,
    snippet_hit_ratio,
    snippet_sha256,
    split_sentences,
    strip_html_tags,
    stratified_sample,
    summarize,
    write_csv,
)


class _StubValidator:
    """最小 validator：只实现 locate，避免依赖真实语料库。"""

    def __init__(self, mapping):
        self._mapping = mapping

    def locate(self, paper_path):
        if paper_path in self._mapping:
            return "exact", self._mapping[paper_path]
        return "missing", None


def test_collapse_number_spaces_merges_typographic_gap():
    assert collapse_number_spaces("1 234.50") == "1234.50"
    assert collapse_number_spaces("营收 12 亿元") == "营收 12 亿元"


def test_split_sentences_keeps_terminators():
    assert split_sentences("净利润为12.5亿元。同比增长3.2%！") == ["净利润为12.5亿元。", "同比增长3.2%！"]


def test_classify_number_three_kinds():
    assert classify_number("2024", "2024年营收") == "年份"
    assert classify_number("12.5", "增长12.5%左右") == "百分比"
    assert classify_number("95.68", "净利润95.68亿元") == "金额或数值"


def test_iter_number_items_covers_table_and_body():
    text = "2024年净利润为95.68亿元。\n| 公司 | 净利润 |\n| --- | --- |\n| 云南白药 | 47.49 |\n"
    items = iter_number_items(text)
    kinds = {(i["数值"], i["来源"]) for i in items}
    assert ("95.68", "正文") in kinds
    assert ("47.49", "表格") in kinds
    assert ("2024", "正文") in kinds
    # 表头文字与分隔行不产生数值
    assert all(i["数值"] != "---" for i in items)


def test_iter_number_items_dedupes_same_number_in_same_sentence():
    items = iter_number_items("营收12亿元，净利润12亿元。")
    assert [i["数值"] for i in items] == ["12"]


def test_iter_number_items_with_lines_records_lineno():
    text = "第一行无数字。\n营收 3.14 亿元。\n"
    items = iter_number_items_with_lines(text)
    assert items and items[0]["行号"] == "2"


def test_snippet_sha256_ignores_whitespace():
    assert snippet_sha256("新华 制药 2024") == snippet_sha256("新华制药2024")
    assert snippet_sha256("") != snippet_sha256("a")


def test_match_hit_refs_normalizes_comma():
    refs = [{"text": "净利润 1,234.5 万元"}, {"text": "无相关数字"}]
    assert match_hit_refs("1234.5", refs) == [1]
    assert match_hit_refs("9999", refs) == []


def test_audit_one_reference_locates_snippet(tmp_path: Path):
    paper = tmp_path / "研报A.md"
    paper.write_text("新华制药 2024 年净利润为 4.75 亿元，同比增长 12.3%。", encoding="utf-8")
    validator = _StubValidator({"研报A.md": str(paper)})
    ref = {"paper_path": "研报A.md", "text": "新华制药 2024 年净利润为 4.75 亿元"}
    detail = audit_one_reference(ref, validator)
    assert detail["文件存在"] == "是"
    assert detail["片段定位"] == "全文命中"
    assert detail["片段sha256"]


def test_audit_one_reference_missing_file(tmp_path: Path):
    validator = _StubValidator({})
    detail = audit_one_reference({"paper_path": "不存在.md", "text": "片段"}, validator)
    assert detail["定位状态"] == "missing"
    assert detail["文件存在"] == "否"
    assert detail["片段定位"] == "未比对（文件缺失）"


def test_audit_one_reference_marks_aggregated_source():
    validator = _StubValidator({})
    detail = audit_one_reference({"paper_path": "聚合表格/多源", "text": "片段"}, validator)
    assert detail["定位状态"] == "aggregated"


def test_audit_one_reference_snippet_not_found_in_existing_file(tmp_path: Path):
    paper = tmp_path / "研报B.md"
    paper.write_text("完全无关的内容。", encoding="utf-8")
    validator = _StubValidator({"研报B.md": str(paper)})
    detail = audit_one_reference({"paper_path": "研报B.md", "text": "这句话不在文件里"}, validator)
    assert detail["片段定位"] == "低命中（token<80%）"


def test_build_checklist_produces_rows_and_columns(tmp_path: Path):
    paper = tmp_path / "研报C.md"
    paper.write_text("云南白药 2024 年营业收入 400.33 亿元。", encoding="utf-8")
    validator = _StubValidator({"研报C.md": str(paper)})
    records = [
        {
            "编号": "B2099",
            "子问题": "云南白药 2024 年营业收入是多少？",
            "答案": "云南白药 2024 年营业收入为 400.33 亿元。",
            "引用": [{"paper_path": "研报C.md", "text": "云南白药 2024 年营业收入 400.33 亿元"}],
        }
    ]
    checklist, citations = build_checklist(records, validator)
    assert checklist and set(checklist[0]) == set(CHECKLIST_COLUMNS)
    assert checklist[0]["编号"] == "B2099"
    assert checklist[0]["引用序号"] == "1"
    assert checklist[0]["人工核对结论"] == ""
    assert citations[0]["定位状态"] == "exact"
    assert citations[0]["引用序号"] == "1"


def test_summarize_counts_states(tmp_path: Path):
    paper = tmp_path / "研报D.md"
    paper.write_text("净利率 20.5%。", encoding="utf-8")
    validator = _StubValidator({"研报D.md": str(paper)})
    records = [
        {
            "编号": "B2098",
            "子问题": "净利率？",
            "答案": "净利率 20.5%。",
            "引用": [{"paper_path": "研报D.md", "text": "净利率 20.5%"}],
            "SQL": "",
            "错误": "",
        }
    ]
    checklist, citations = build_checklist(records, validator)
    summary = summarize(records, checklist, citations)
    assert summary["样本题数"] == 1
    assert summary["待核对数值条目"] == 1
    assert summary["引用定位状态分布"] == {"exact": 1}
    assert "非自动结论" in summary["口径声明"]


def test_stratified_sample_covers_type_and_sql():
    items = []
    for i in range(1, 9):
        items.append({"编号": f"Q{i:04d}", "问题类型": "多意图" if i <= 4 else "归因分析",
                      "子问题": [f"问题{i}"]})
    sql_flags = {f"Q{i:04d}": i % 2 == 0 for i in range(1, 9)}
    samples = stratified_sample(items, sql_flags, n=6)
    assert len(samples) == 6
    assert len({s["分层键"] for s in samples}) == 4
    assert len({s["编号"] for s in samples}) == 6
    assert all("问题" in s["子问题"] for s in samples)


def test_stratified_sample_respects_sub_index():
    items = [{"编号": "Q0001", "问题类型": "多意图", "子问题": ["第一问", "第二问"]}]
    samples = stratified_sample(items, {"Q0001": True}, n=1, sub_index=1)
    assert samples[0]["子问题"] == "第二问"


def test_write_csv_uses_bom_and_columns(tmp_path: Path):
    out = tmp_path / "out.csv"
    write_csv(out, list(CHECKLIST_COLUMNS), [dict.fromkeys(CHECKLIST_COLUMNS, "")])
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    header = raw.decode("utf-8-sig").splitlines()[0]
    assert header.split(",")[:5] == ["编号", "子问题", "数值", "所在句子", "引用序号"]


def test_iter_number_items_handles_empty_text():
    assert iter_number_items("") == []
    assert iter_number_items(None) == []  # type: ignore[arg-type]

def test_strip_html_tags_removes_table_markup():
    assert strip_html_tags("<td>中航证券</td><td>2024</td>").split() == ["中航证券", "2024"]
    assert strip_html_tags("<tr><td>a</td></tr>") == "  a  "


def test_snippet_hit_ratio_counts_tokens():
    content = "中航证券研究所发布 证券研究报告"
    ratio, hits, total = snippet_hit_ratio("中航证券研究所发布 证券研究报告", content)
    assert (hits, total) == (2, 2) and ratio == 1.0


def test_snippet_hit_ratio_ignores_short_tokens():
    ratio, hits, total = snippet_hit_ratio("a b c", "a b c")
    assert (hits, total) == (0, 0) and ratio == 0.0


def test_audit_one_reference_matches_html_table_row(tmp_path: Path):
    """MinerU 表格存成 HTML，引用为单元格拼接文本时应判「去HTML后命中」。"""
    paper = tmp_path / "研报E.md"
    paper.write_text(
        "<tr><td>中航证券研究所发布</td><td></td><td>证券研究报告</td></tr>", encoding="utf-8"
    )
    validator = _StubValidator({"研报E.md": str(paper)})
    detail = audit_one_reference(
        {"paper_path": "研报E.md", "text": "中航证券研究所发布    证券研究报告"}, validator
    )
    assert detail["片段定位"] == "去HTML后命中"
    assert detail["片段命中率"] == "1.00"


def test_audit_one_reference_partial_hit_for_stitched_rows(tmp_path: Path):
    paper = tmp_path / "研报F.md"
    paper.write_text(
        "<td>佐力药业</td><td>0.39</td><td>2.73</td><td>万股</td>", encoding="utf-8"
    )
    validator = _StubValidator({"研报F.md": str(paper)})
    detail = audit_one_reference(
        {"paper_path": "研报F.md", "text": "佐力药业 0.39 2.73 万股 不存在的列"}, validator
    )
    assert detail["片段定位"] == "部分命中（token≥80%）"
    assert float(detail["片段命中率"]) == 0.8
    assert detail["命中token"] == "4/5"
