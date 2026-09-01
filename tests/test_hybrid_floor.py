"""core.retrievers.HybridRetriever 的 vector_floor_ratio 保底逻辑单测。"""

from core.retrievers import BM25Retriever, HybridRetriever


class _FakeVectorRetriever:
    """固定顺序的伪向量检索器（模拟 HandwrittenRetriever）。"""

    def __init__(self, ids):
        self.ids = ids

    def retrieve(self, query, query_filter=None, top_k=None):
        return [
            {"id": idx, "score": 0.9 - rank * 0.1, "payload": {"content": f"doc{idx}"}}
            for rank, idx in enumerate(self.ids[: (top_k or len(self.ids))])
        ]


def _sample_docs():
    return [
        {"id": 1, "payload": {"content": "贵州茅台2024年营收增长15%，每股收益eps大幅提升"}},
        {"id": 2, "payload": {"content": "五粮液2023年净利润下降，eps承压，白酒行业分化"}},
        {"id": 3, "payload": {"content": "半导体行业景气度回升，龙头公司订单饱满"}},
        {"id": 4, "payload": {"content": "医药板块行情回暖，创新药管线兑现"}},
    ]


def _make(ratio, vector_ids, top_k=3):
    return HybridRetriever(
        vector_retriever=_FakeVectorRetriever(vector_ids),
        bm25_retriever=BM25Retriever(docs=_sample_docs()),
        top_k=top_k,
        rrf_k=60,
        topk_vector=50,
        topk_bm25=50,
        vector_floor_ratio=ratio,
    )


def test_floor_zero_is_pure_rrf():
    # 无保底：BM25 独有高分文档可以进入 top-K（4 号文档含关键词"医药"但不含查询词时除外，这里用查询命中 1/2/3）
    hybrid = _make(0.0, [1, 2, 3], top_k=3)
    hits = hybrid.retrieve("贵州茅台 eps")
    assert [h["id"] for h in hits][:2] == [1, 2]  # 交集优先


def test_floor_keeps_vector_docs_on_top():
    # 保底比例=1.0：top-K 全部来自向量路，BM25 独有文档（doc4 医药）被挤出
    hybrid = _make(1.0, [3, 2, 1], top_k=3)
    hits = hybrid.retrieve("医药 茅台")
    ids = [h["id"] for h in hits]
    assert len(ids) == 3
    assert set(ids) == {1, 2, 3}  # 向量路三篇都在，BM25 独有 doc4 不占位
    assert 4 not in ids


def test_floor_partial_mixes_both():
    # 保底 2/3：前 2 条来自向量路，第 3 条可为 BM25 独有
    hybrid = _make(2.0 / 3.0, [1, 3], top_k=3)
    hits = hybrid.retrieve("贵州茅台 eps")
    ids = [h["id"] for h in hits]
    assert len(ids) == 3
    assert ids[0] in (1, 3) and ids[1] in (1, 3)  # 前两条为向量路
