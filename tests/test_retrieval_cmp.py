# -*- coding: utf-8 -*-
"""eval.retrieval_cmp 的纯逻辑单测（数字匹配 / 文件命中 / 聚合），零外部依赖。"""

from eval.retrieval_cmp import NumberMatcher, _candidate_files, _rate, _summarize


def test_number_matcher_extract():
    m = NumberMatcher("comma")
    assert m.extract("营收增长11.6%，净利润1,234.5万元") == ["11.6", "1", "234.5"]
    assert m.extract("无数字文本") == []


def test_number_matcher_comma_normalization():
    m = NumberMatcher("comma")
    hit, unhit = m.count_hits(["1,234.5"], ["公司净利润 1234.5 万元"])
    assert hit == 1 and unhit == []


def test_number_matcher_loose_ignores_whitespace():
    m = NumberMatcher("loose")
    hit, _ = m.count_hits(["11.6"], ["营收增长 11.6 %"])
    assert hit == 1


def test_candidate_files_basename():
    results = [
        {"payload": {"file_path": "D:/x/个股研报/华润三九.md"}},
        {"payload": {"file_path": "五粮液.md"}},
        {"payload": {"file_path": ""}},
    ]
    files = _candidate_files(results)
    assert files == {"华润三九.md", "五粮液.md"}


def test_rate():
    assert _rate(2, 4) == 0.5
    assert _rate(0, 0) is None


def _fake_row(bid, vf, hf, ft, vn, hn, nt):
    return {
        "bid": bid, "qtype": "归因分析", "query": "q",
        "files_total": ft, "vec_files_hit": vf, "hyb_files_hit": hf,
        "num_total": nt, "vec_num_hit": vn, "hyb_num_hit": hn,
        "vec_file_hit": [], "hyb_file_hit": [], "vec_unhit": [], "hyb_unhit": [],
        "vec_top_files": [], "hyb_top_files": [],
    }


def test_summarize_aggregates_and_winners():
    rows = [
        _fake_row("B2001", 1, 2, 4, 1, 3, 4),   # hybrid 双胜
        _fake_row("B2002", 2, 2, 4, 2, 2, 4),   # 持平
        _fake_row("B2003", 2, 1, 4, 3, 2, 4),   # vector 双胜
    ]
    s = _summarize(rows)
    assert s["rows"] == 3 and s["bids"] == 3
    assert s["file"]["vec"]["hit"] == 5 and s["file"]["hybrid"]["hit"] == 5
    assert s["file"]["win"] == {"hybrid_win": 1, "tie": 1, "vector_win": 1}
    assert s["num"]["win"] == {"hybrid_win": 1, "tie": 1, "vector_win": 1}
    assert s["file"]["all_hit_rows"] == 0
    assert s["file"]["any_hit_rows"] == 3


def test_summarize_empty():
    s = _summarize([])
    assert s["rows"] == 0

def test_summarize_rerank_section():
    row = _fake_row("B2001", 1, 2, 4, 1, 3, 4)
    row.update({
        "vec_rr_files_hit": 2,
        "hyb_rr_files_hit": 4,
        "vec_rr_num_hit": 1,
        "hyb_rr_num_hit": 3,
        "vec_rr_unhit": [],
        "hyb_rr_unhit": [],
    })
    s = _summarize([row], rerank_top_n=10)
    assert s["rerank"]["file"]["vec"]["hit"] == 2
    assert s["rerank"]["file"]["hybrid"]["hit"] == 4
    assert s["rerank"]["num"]["vec"]["hit"] == 1
    assert s["rerank"]["num"]["hybrid"]["hit"] == 3
    assert s["file"]["vec"]["hit"] == 1


class _FakeRerankClient:
    """固定返回两条精排结果的伪 Rerank 客户端（验证 index 保留与 payload 映射）。"""

    def rerank(self, query, documents, top_n):
        return [
            {"index": 3, "relevance_score": 0.9, "document": documents[3]},
            {"index": 1, "relevance_score": 0.8, "document": documents[1]},
        ]


def test_rerank_entries_preserves_index_and_payload():
    from eval.retrieval_cmp import _rerank_entries
    results = [
        {"id": 1, "payload": {"content": "c1", "file_path": "a.md"}},
        {"id": 2, "payload": {"content": "c2", "file_path": "b.md"}},
        {"id": 3, "payload": {"content": "c3", "file_path": "c.md"}},
        {"id": 4, "payload": {"content": "c4", "file_path": "d.md"}},
    ]
    out = _rerank_entries(_FakeRerankClient(), "q", results, 2)
    assert [e["index"] for e in out] == [3, 1]
    assert out[0]["payload"]["file_path"] == "d.md"
    assert out[1]["payload"]["file_path"] == "b.md"
