"""一致性套件离线单测（B-25A）——零外部依赖，不调用 LLM / Qdrant / MySQL"""

import json
from pathlib import Path

from eval.consistency import (
    aggregate_all,
    aggregate_question,
    extract_number_set,
    jaccard,
    load_reuse_runs,
    mean_pairwise,
    ref_keys,
    signature_key,
    sql_contract_rate,
    structure_consistency,
    structure_signature,
    stratified_sample,
)


def test_extract_number_set_handles_typographic_spaces():
    assert extract_number_set("营收 1 234.5 万元，增长 12.5%") == {"1234.5", "12.5"}


def test_extract_number_set_on_empty_text():
    assert extract_number_set("") == set()
    assert extract_number_set(None) == set()  # type: ignore[arg-type]


def test_jaccard_basic_and_empty():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a"}, {"b"}) == 0.0
    assert jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert jaccard(set(), set()) == 1.0


def test_mean_pairwise_requires_two_sets():
    assert mean_pairwise([{"a"}]) is None
    assert mean_pairwise([{"a"}, {"a"}]) == 1.0
    assert mean_pairwise([{"a"}, {"b"}, {"a"}]) == round((0.0 + 1.0 + 0.0) / 3, 4)


def test_ref_keys_normalizes_punctuation_and_head():
    refs = [{"paper_path": "研报 A.md", "text": "净利润 4.75 亿元。后续内容"}]
    keys = ref_keys(refs, head_chars=8)
    assert len(keys) == 1
    assert "研报A.md||" in next(iter(keys))


def test_structure_signature_detects_refuse_table_and_chart():
    text = "# 结论\n某指标未包含在本次查询范围内。\n| a | b |\n| --- | --- |\n| 1 | 2 |\necharts"
    sig = structure_signature(text)
    assert sig["拒答"] is True
    assert sig["有表格"] is True
    assert sig["有图表"] is True
    assert sig["标题数"] == 1


def test_structure_signature_length_buckets():
    assert structure_signature("短")["长度桶"].startswith("短")
    assert structure_signature("x" * 300)["长度桶"].startswith("中")
    assert structure_signature("x" * 800)["长度桶"].startswith("长")
    assert structure_signature("x" * 2000)["长度桶"].startswith("超长")


def test_structure_consistency_modal_and_refuse_agreement():
    runs = [
        {"答案": "# 结论\n数据未包含。"},
        {"答案": "# 结论\n数据未包含。"},
        {"答案": "另一套结构"},
    ]
    res = structure_consistency(runs)
    assert res["指纹一致率"] == round(2 / 3, 4)
    assert res["拒答一致"] is False
    assert res["多数指纹"] == signature_key(structure_signature("# 结论\n数据未包含。"))


def test_structure_consistency_handles_empty_runs():
    res = structure_consistency([])
    assert res["指纹一致率"] is None and res["拒答一致"] is None


def test_sql_contract_rate_returns_none_without_sql():
    assert sql_contract_rate([{"SQL": ""}, {"SQL": "  "}]) is None


def test_sql_contract_rate_counts_contract_result():
    ok_sql = "SELECT stock_abbr FROM core_performance_indicators_sheet LIMIT 10;"
    bad_sql = "DROP TABLE core_performance_indicators_sheet;"
    rate = sql_contract_rate([{"SQL": ok_sql}, {"SQL": bad_sql}])
    assert rate == 0.5


def test_aggregate_question_and_all_produce_baseline_numbers():
    questions = [
        {
            "编号": "Q0001", "问题类型": "多意图", "分层键": "多意图|有SQL", "子问题": "问题",
            "runs": [
                {"答案": "净利润 1.5 亿元。", "引用": [{"paper_path": "A.md", "text": "净利润 1.5 亿元"}], "SQL": ""},
                {"答案": "净利润 1.5 亿元。", "引用": [{"paper_path": "A.md", "text": "净利润 1.5 亿元"}], "SQL": ""},
            ],
        }
    ]
    row = aggregate_question(questions[0])
    assert row["数值一致性(IoU均值)"] == 1.0
    assert row["引用一致性(Jaccard均值)"] == 1.0
    assert row["结构一致性(指纹一致率)"] == 1.0
    summary = aggregate_all(questions)
    assert summary["数值一致性为1.0的题数"] == 1
    assert summary["结构指纹全一致的题数"] == 1
    assert "一致性低 ≠ 答案错误" in summary["口径声明"]


def test_stratified_sample_deterministic_covers_buckets():
    items = []
    for i in range(1, 7):
        items.append({"编号": f"Q{i:04d}", "问题类型": "A" if i <= 3 else "B", "子问题": [f"q{i}"]})
    flags = {f"Q{i:04d}": i % 2 == 0 for i in range(1, 7)}
    first = stratified_sample(items, flags, count=4)
    again = stratified_sample(items, flags, count=4)
    assert [s["编号"] for s in first] == [s["编号"] for s in again]  # 默认确定性
    assert len({s["分层键"] for s in first}) == 4
    seeded = stratified_sample(items, flags, count=4, seed=7)
    assert len({s["编号"] for s in seeded}) == 4


def test_load_reuse_runs_reads_generation_artifact(tmp_path: Path):
    payload = {
        "样本": [
            {"编号": "Q0001", "答案": "答", "引用": [{"paper_path": "A.md", "text": "x"}], "SQL": "SELECT 1;"}
        ]
    }
    path = tmp_path / "raw.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    reuse = load_reuse_runs(path)
    assert reuse["Q0001"]["答案"] == "答"
    assert reuse["Q0001"]["来源"].startswith("reuse:")


def test_load_reuse_runs_raises_on_missing_file(tmp_path: Path):
    try:
        load_reuse_runs(tmp_path / "nope.json")
    except FileNotFoundError:
        return
    raise AssertionError("应抛 FileNotFoundError")