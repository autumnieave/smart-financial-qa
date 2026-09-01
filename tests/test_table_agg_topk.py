"""表聚合 top-K 收敛单测（零外部依赖：桩 Qdrant，验证仅 top-K 表格行触发父表 scroll）。

对应 _aggregate_parent_table 的 TABLE_AGG_TOPK 配置：低相关表格行不再拉父表，
直接保持片段原样，降低首查多表 scroll 耗时。
"""

import threading
from types import SimpleNamespace

from pipelines.rag_pipeline import RAGPipeline


class _QdrantStub:
    """最小 Qdrant 桩：models 静态构造 + client.scroll 按 parent_id 返回行点"""

    class models:
        @staticmethod
        def Filter(**kwargs):
            return kwargs

        @staticmethod
        def FieldCondition(**kwargs):
            return kwargs

        @staticmethod
        def MatchValue(**kwargs):
            return kwargs

    def __init__(self, tables):
        # tables: parent_id -> [payload dict, ...]（行点）
        self.tables = tables
        self.scroll_calls = []

    @property
    def client(self):
        return self

    def scroll(self, collection_name, scroll_filter, limit, with_payload, with_vectors):
        pid = scroll_filter["must"][0]["match"]["value"]
        self.scroll_calls.append(pid)
        points = self.tables.get(pid, [])
        return (
            [SimpleNamespace(payload=pl) for pl in points],
            None,
        )


def _row(pid, idx, text):
    # 返回裸 payload（搜索结果的 payload 由调用方包裹）
    return {"is_table_row": True, "parent_id": pid, "row_index": idx, "content": text, "source": "a.md"}


def _doc(text):
    return {"is_table_row": False, "content": text, "source": "b.md"}


def _wrap(entries):
    return [{"payload": e} for e in entries]


def _pipeline(topk, tables):
    p = RAGPipeline.__new__(RAGPipeline)
    p.config = SimpleNamespace(TABLE_AGG_TOPK=topk, QDRANT_COLLECTION_NAME="test_col")
    p.qdrant_client = _QdrantStub(tables)
    p._table_agg_cache = {}
    p._table_agg_lock = threading.Lock()
    return p


def test_only_topk_rows_trigger_scroll():
    tables = {
        1: [_row(1, 0, "表A行1"), _row(1, 1, "表A行2")],
        2: [_row(2, 0, "表B行1")],
        3: [_row(3, 0, "表C行1")],
    }
    p = _pipeline(topk=2, tables=tables)
    search_results = _wrap([_row(1, 0, "表A行1"), _row(2, 0, "表B行1"), _doc("正文1"), _doc("正文2"), _row(3, 0, "表C行1")])
    candidate_docs = [r["payload"]["content"] for r in search_results]

    new_docs, _, _, index_map = p._aggregate_parent_table(search_results, candidate_docs)

    # 仅 top-2 内的表格行（表A/表B）触发 scroll；表C（第5位）不拉父表
    assert set(p.qdrant_client.scroll_calls) == {1, 2}
    # 表A/表B 被聚合为完整表格，表C 行保持片段原样
    joined = "\n".join(new_docs)
    assert "表A行2" in joined and "表B行1" in joined
    assert "表C行1" in joined
    assert new_docs[0] == "表A行1\n表A行2"
    # index_map：新候选位置 -> 原始 search_results 索引（0=聚合表A←原0，1=聚合表B←原1，2=正文1←原2，3=正文2←原3，4=表C行←原4）
    assert index_map == {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}


def test_topk_zero_means_no_limit():
    tables = {1: [_row(1, 0, "行A")], 2: [_row(2, 0, "行B")]}
    p = _pipeline(topk=0, tables=tables)
    search_results = _wrap([_row(1, 0, "行A"), _row(2, 0, "行B")])
    candidate_docs = [r["payload"]["content"] for r in search_results]
    _, _, _, index_map = p._aggregate_parent_table(search_results, candidate_docs)
    assert set(p.qdrant_client.scroll_calls) == {1, 2}
    assert index_map == {0: 0, 1: 1}


def test_cache_prevents_repeat_scroll():
    tables = {1: [_row(1, 0, "行A")]}
    p = _pipeline(topk=1, tables=tables)
    search_results = _wrap([_row(1, 0, "行A")])
    candidate_docs = [r["payload"]["content"] for r in search_results]
    p._aggregate_parent_table(search_results, candidate_docs)
    p._aggregate_parent_table(search_results, candidate_docs)
    assert p.qdrant_client.scroll_calls == [1]


def test_build_reference_uses_index_map():
    """引用构建必须按 index_map 回映射原始 search_results，避免聚合后 paper_path 取错文件"""
    p = _pipeline(topk=2, tables={1: [_row(1, 0, "表A行1"), _row(1, 1, "表A行2")]})
    search_results = [
        {"payload": {"is_table_row": True, "parent_id": 1, "content": "表A行1", "source": "a.md"}},
        {"payload": {"is_table_row": False, "content": "正文来自 b", "source": "b.md", "summary": "b 摘要"}},
    ]
    candidate_docs = [r["payload"]["content"] for r in search_results]
    new_docs, _, aggregated_meta, index_map = p._aggregate_parent_table(search_results, candidate_docs)
    # 聚合后：位置0=聚合表A（原索引0），位置1=正文（原索引1）
    ref = p._build_reference_for_doc(1, search_results, new_docs, aggregated_meta, index_map)
    assert ref["paper_path"] == "b.md"
    assert ref["text"] == "b 摘要"
    # 若不传 index_map（旧行为），会错取 search_results[1] 的 payload（正文在位置1恰好对齐）；
    # 关键回归：聚合表位置0 回映射到原索引0
    ref0 = p._build_reference_for_doc(0, search_results, new_docs, aggregated_meta, index_map)
    assert ref0["paper_path"] == "a.md"
