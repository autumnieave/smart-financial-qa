"""core.retrievers 的 BM25 / 混合检索（RRF 融合）纯逻辑单测（#8 实验链路）。

不依赖外部服务：直接用小语料构造 BM25 索引与伪向量检索器。
"""

from core.retrievers import BM25Retriever, HybridRetriever


def _sample_docs():
    return [
        {"id": 1, "payload": {"content": "贵州茅台2024年营收增长15%，每股收益eps大幅提升"}},
        {"id": 2, "payload": {"content": "五粮液2023年净利润下降，eps承压，白酒行业分化"}},
        {"id": 3, "payload": {"content": "半导体行业景气度回升，龙头公司订单饱满"}},
    ]


def test_bm25_relevant_first():
    bm25 = BM25Retriever(docs=_sample_docs())
    hits = bm25.retrieve("贵州茅台 eps", top_k=2)
    assert len(hits) == 2
    assert hits[0]["id"] == 1
    assert hits[0]["score"] > hits[1]["score"]


def test_bm25_no_overlap_returns_empty():
    bm25 = BM25Retriever(docs=_sample_docs())
    assert bm25.retrieve("完全不相关的词汇xyz", top_k=3) == []


def test_bm25_tokenize_mixed():
    tokens = BM25Retriever.tokenize("贵州茅台2024年报")
    assert "2024" in tokens
    assert "贵" in tokens


class _FakeVectorRetriever:
    """固定返回顺序的伪向量检索器（模拟 HandwrittenRetriever 的返回结构）。"""

    def __init__(self, ids):
        self.ids = ids

    def retrieve(self, query, query_filter=None, top_k=None):
        return [
            {"id": idx, "score": 0.9 - rank * 0.1, "payload": {"content": f"doc{idx}"}}
            for rank, idx in enumerate(self.ids[: (top_k or len(self.ids))])
        ]


def test_hybrid_rrf_intersection_first_and_dedup():
    vector = _FakeVectorRetriever([1, 2, 3])
    bm25 = BM25Retriever(docs=_sample_docs())
    hybrid = HybridRetriever(
        vector_retriever=vector,
        bm25_retriever=bm25,
        top_k=5,
        rrf_k=60,
        topk_vector=50,
        topk_bm25=50,
    )
    hits = hybrid.retrieve("贵州茅台 eps")
    ids = [h["id"] for h in hits]
    # 两路都命中的 doc1/doc2 排在前，且按 id 去重不重复
    assert ids[0] == 1 and ids[1] == 2 and ids[2] == 3
    assert len(ids) == len(set(ids))
    assert hits[0]["score"] > hits[2]["score"]


def test_hybrid_falls_back_to_vector_when_bm25_empty():
    vector = _FakeVectorRetriever([3])
    bm25 = BM25Retriever(docs=_sample_docs())
    hybrid = HybridRetriever(vector_retriever=vector, bm25_retriever=bm25, top_k=5)
    hits = hybrid.retrieve("完全不相关的词汇xyz")
    assert [h["id"] for h in hits] == [3]

class _RecordingRetriever:
    """记录每次 retrieve 收到的 top_k（验证双腿随 limit 缩放）。"""

    def __init__(self, ids):
        self.ids = ids
        self.seen_top_k = []

    def retrieve(self, query, query_filter=None, top_k=None):
        self.seen_top_k.append(top_k)
        return [
            {"id": idx, "score": 1.0, "payload": {"content": f"doc{idx}"}}
            for idx in self.ids[: (top_k or len(self.ids))]
        ]


def test_hybrid_legs_scale_with_top_k():
    vec = _RecordingRetriever([1, 2, 3])
    bm = _RecordingRetriever([4, 5, 6])
    hybrid = HybridRetriever(
        vector_retriever=vec,
        bm25_retriever=bm,
        top_k=5,
        topk_vector=3,
        topk_bm25=4,
    )
    hybrid.retrieve("q", top_k=10)
    assert vec.seen_top_k == [10]
    assert bm.seen_top_k == [10]
