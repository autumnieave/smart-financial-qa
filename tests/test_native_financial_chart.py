# -*- coding: utf-8 -*-
"""tests/test_native_financial_chart.py —— 原生财务链路图表纯函数单测（零外部依赖）。

覆盖 tools/native_financial.py 的 _to_num / _sanitize_chart：
- _to_num：数据点数值归一（字符串数字转 float，非数值原样保留）
- _sanitize_chart：ECharts option 校验（必须含非空 series[].data、超限截断、数值归一）
"""

from tools.native_financial import _sanitize_chart, _to_num


class TestToNum:
    """_to_num 数值归一。"""

    def test_int_passthrough(self):
        assert _to_num(42) == 42

    def test_float_passthrough(self):
        assert _to_num(42.5) == 42.5

    def test_numeric_string(self):
        assert _to_num("9387.75") == 9387.75

    def test_bool_not_coerced(self):
        assert _to_num(True) is True

    def test_non_numeric_string_kept(self):
        assert _to_num("abc") == "abc"

    def test_none_kept(self):
        assert _to_num(None) is None


class TestSanitizeChart:
    """_sanitize_chart ECharts option 校验与收敛。"""

    def test_valid_option_passthrough(self):
        option = {"title": {"text": "t"}, "series": [{"name": "净利润", "data": [1, 2, 3]}]}
        out = _sanitize_chart(option)
        assert out is not None
        assert out["series"][0]["data"] == [1, 2, 3]

    def test_missing_series_rejected(self):
        assert _sanitize_chart({"title": {"text": "t"}}) is None

    def test_empty_series_rejected(self):
        assert _sanitize_chart({"series": []}) is None

    def test_series_without_data_rejected(self):
        assert _sanitize_chart({"series": [{"name": "x"}]}) is None

    def test_empty_data_rejected(self):
        assert _sanitize_chart({"series": [{"name": "x", "data": []}]}) is None

    def test_not_dict_rejected(self):
        assert _sanitize_chart("nope") is None

    def test_numeric_string_coerced(self):
        option = {"series": [{"name": "x", "data": ["1.5", 2, "3"]}]}
        out = _sanitize_chart(option)
        assert out["series"][0]["data"] == [1.5, 2, 3]

    def test_data_capped_at_200(self):
        option = {"series": [{"name": "x", "data": list(range(300))}]}
        out = _sanitize_chart(option)
        assert len(out["series"][0]["data"]) == 200
