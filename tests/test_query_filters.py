"""filters.query_filters 软过滤打分单元测试。"""

from filters.query_filters import QueryFilters


def test_no_filter_scores_full():
    qf = QueryFilters()
    assert qf.compute_match_score({"stockName": "贵州茅台"}) == 1.0


def test_stock_name_match():
    qf = QueryFilters(stock_name="贵州茅台")
    assert qf.compute_match_score({"stockName": "贵州茅台"}) > qf.compute_match_score({"stockName": "五粮液"})


def test_stock_code_match():
    qf = QueryFilters(stock_code="600519")
    assert qf.compute_match_score({"stockCode": "600519"}) == 1.0


def test_multi_field_score_ordering():
    qf = QueryFilters(stock_name="贵州茅台", org_name="中信证券")
    full = qf.compute_match_score({"stockName": "贵州茅台", "orgName": "中信证券"})
    half = qf.compute_match_score({"stockName": "贵州茅台", "orgName": "其他券商"})
    none = qf.compute_match_score({"stockName": "五粮液", "orgName": "其他券商"})
    assert full > half > none
