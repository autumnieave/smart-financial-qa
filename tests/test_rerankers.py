# -*- coding: utf-8 -*-
"""core.rerankers 的每文件多样性选择单测（零外部依赖）。"""
from core.rerankers import apply_file_diversity, file_keys_from_candidates


def _rr(indices):
    return [{"index": i, "relevance_score": 1.0, "document": f"d{i}"} for i in indices]


def test_file_keys_from_candidates_basename():
    cands = [
        {"payload": {"file_path": "D:/x/个股研报/华润三九.md"}},
        {"payload": {"file_path": ""}},
        {"payload": {}},
    ]
    assert file_keys_from_candidates(cands) == ["华润三九.md", "", ""]


def test_diversity_cap_one_dedupes_per_file():
    rr = _rr([0, 1, 2, 3])
    keys = ["A.md", "A.md", "B.md", "C.md"]
    sel = apply_file_diversity(rr, keys, top_n=3, max_per_file=1)
    assert [s["index"] for s in sel] == [0, 2, 3]


def test_diversity_cap_two_keeps_two_then_other_file():
    rr = _rr([0, 1, 2, 3])
    keys = ["A.md", "A.md", "A.md", "B.md"]
    sel = apply_file_diversity(rr, keys, top_n=3, max_per_file=2)
    assert [s["index"] for s in sel] == [0, 1, 3]


def test_diversity_off_returns_top_n():
    rr = _rr([0, 1, 2, 3])
    keys = ["A.md"] * 4
    sel = apply_file_diversity(rr, keys, top_n=2, max_per_file=0)
    assert [s["index"] for s in sel] == [0, 1]


def test_diversity_short_candidates_no_crash():
    rr = _rr([0, 1])
    sel = apply_file_diversity(rr, ["A.md", "A.md"], top_n=5, max_per_file=1)
    assert [s["index"] for s in sel] == [0]