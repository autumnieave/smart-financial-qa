"""tools.sql_guard（SQL 校验重问守卫）单元测试。

全部离线：mock ask / schema / conn，不依赖 MySQL / LLM 等外部服务。
"""

import types
from typing import List, Optional, Tuple

from tools.sql_guard import call_with_guard, sql_errors


FAKE_SCHEMA = {
    "income_sheet": {
        "net_profit": "decimal(10,4)",
        "total_operating_revenue": "decimal(10,4)",
        "total_operating_expenses": "decimal(10,4)",
        "net_profit_yoy_growth": "decimal(10,4)",
    },
}

VALID_SQL = "SELECT t1.net_profit AS p FROM income_sheet t1 LIMIT 3"
INVALID_SQL = "SELECT t2.net_profit AS p FROM income_sheet t1 LIMIT 3"


class Recorder:
    """记录每次 ask 的问题，并按预设序列返回 (analysis, image, sql)。"""

    def __init__(self, responses: List[Tuple[str, Optional[str], str]]):
        self._responses = list(responses)
        self.asked: List[str] = []

    def ask(self, question: str, user_id: str) -> Tuple[str, Optional[str], str]:
        self.asked.append(question)
        analysis, img, sql = self._responses.pop(0)
        return analysis, img, sql


def test_valid_sql_no_retry():
    """SQL 一次通过：只调用一次 ask，剩余错误为空。"""
    rec = Recorder([("答案A", None, VALID_SQL)])
    analysis, img, sql, left = call_with_guard(
        rec.ask, "问题", "u1", schema=FAKE_SCHEMA, retries=1
    )
    assert analysis == "答案A"
    assert sql == VALID_SQL
    assert left == []
    assert len(rec.asked) == 1


def test_invalid_then_valid_retries_once():
    """第一次坏 SQL 触发带提示重问，第二次通过。"""
    rec = Recorder([
        ("答案坏", None, INVALID_SQL),
        ("答案好", None, VALID_SQL),
    ])
    _, _, sql, left = call_with_guard(
        rec.ask, "2024年谁最赚钱", "u1", schema=FAKE_SCHEMA, retries=1
    )
    assert sql == VALID_SQL
    assert left == []
    assert len(rec.asked) == 2
    assert "上次生成的 SQL 有误" in rec.asked[1]
    assert "未定义别名" in rec.asked[1]


def test_retry_hint_includes_field_suggestions():
    """字段不存在错误会在重问提示里给出该表的可用字段参考。"""
    bad = "SELECT t1.total_operating_expenses_yoy_growth AS g FROM income_sheet t1"
    rec = Recorder([("答案坏", None, bad), ("答案好", None, VALID_SQL)])
    _, _, sql, left = call_with_guard(
        rec.ask, "营业总成本同比增长多少", "u1", schema=FAKE_SCHEMA, retries=1
    )
    assert sql == VALID_SQL
    assert left == []
    assert len(rec.asked) == 2
    assert "可用字段参考" in rec.asked[1]
    assert "income_sheet.total_operating_expenses" in rec.asked[1]
    assert "income_sheet.net_profit_yoy_growth" in rec.asked[1]
    assert "同比/环比(yoy/qoq)字段仅存在以下白名单" in rec.asked[1]
    assert "禁止编造任何 *_yoy_growth" in rec.asked[1]


def test_invalid_twice_exhausts_keeps_errors():
    """重试耗尽仍失败：返回最后一次 SQL + 剩余错误（诚实上报）。"""
    rec = Recorder([("答案坏1", None, INVALID_SQL), ("答案坏2", None, INVALID_SQL)])
    _, _, sql, left = call_with_guard(
        rec.ask, "问题", "u1", schema=FAKE_SCHEMA, retries=1
    )
    assert sql == INVALID_SQL
    assert left != []
    assert any("未定义别名" in e for e in left)
    assert len(rec.asked) == 2


def test_empty_sql_skips_validation():
    """无 SQL（意图模糊/无需查询）不触发校验与重问。"""
    rec = Recorder([("模型未返回有效内容", None, "")])
    _, _, sql, left = call_with_guard(
        rec.ask, "问题", "u1", schema=FAKE_SCHEMA, retries=1
    )
    assert sql == ""
    assert left == []
    assert len(rec.asked) == 1


def test_fullwidth_comma_rejected_without_schema():
    """无 schema 时静态校验跳过，但全角标点启发式仍拦截并触发重问。"""
    bad = "SELECT t1.a AS 甲，t1.b AS 乙 FROM income_sheet t1"
    rec = Recorder([("答案坏", None, bad), ("答案好", None, VALID_SQL)])
    _, _, sql, left = call_with_guard(
        rec.ask, "问题", "u1", schema=None, retries=1
    )
    assert sql == VALID_SQL
    assert left == []
    assert len(rec.asked) == 2
    assert "全角标点" in rec.asked[1]


def test_schema_none_does_not_crash():
    """schema=None 且无全角标点：不崩溃、不误报。"""
    errs = sql_errors(VALID_SQL, schema=None)
    assert errs == []


class _FakeCursor:
    def execute(self, *args, **kwargs):
        raise RuntimeError("syntax error near 全角逗号")

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()


def test_compile_error_reported_when_conn_given():
    """给定 conn 时，MySQL 编译错误被写入错误列表。"""
    errs = sql_errors(VALID_SQL, schema=FAKE_SCHEMA, conn=_FakeConn())
    assert any("MySQL 编译失败" in e for e in errs)
