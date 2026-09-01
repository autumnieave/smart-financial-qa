"""utils.query_cache 查询缓存单元测试（纯逻辑，SQLite 本地，不依赖外部服务）。"""

import time

from utils.query_cache import SQLiteQueryCache, make_cache_key


def test_make_cache_key_deterministic():
    k1 = make_cache_key("fin-native", "user-1", "云南白药2024年营收")
    k2 = make_cache_key("fin-native", "user-1", "云南白药2024年营收")
    assert k1 == k2
    assert len(k1) == 40  # sha1 hex


def test_make_cache_key_differs():
    assert make_cache_key("chat", "rag", "u1", "q") != make_cache_key("chat", "rag", "u1", "q2")


def test_set_get_roundtrip(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=3600)
    key = make_cache_key("fin-native", "u1", "q1")
    data = {"content": "分析文本", "image": ["result/a.jpg"], "sql": "SELECT 1"}
    cache.set(key, data)
    assert cache.get(key) == data


def test_get_miss_returns_none(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=3600)
    assert cache.get(make_cache_key("x", "nobody")) is None


def test_expired_entry_removed(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=1)
    key = make_cache_key("fin-native", "u1", "q")
    cache.set(key, {"content": "x"}, ttl=1)
    assert cache.get(key) == {"content": "x"}
    time.sleep(1.1)
    assert cache.get(key) is None


def test_ttl_zero_never_expires(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=1)
    key = make_cache_key("fin-native", "u1", "q")
    cache.set(key, {"content": "x"}, ttl=0)
    time.sleep(0.2)
    assert cache.get(key) == {"content": "x"}


def test_clear_removes_all(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=3600)
    cache.set(make_cache_key("a"), {"content": "1"})
    cache.set(make_cache_key("b"), {"content": "2"})
    assert cache.clear() == 2
    assert cache.get(make_cache_key("a")) is None


def test_bad_payload_returns_none(tmp_path):
    cache = SQLiteQueryCache(db_path=str(tmp_path / "qc.db"), ttl=3600)
    key = make_cache_key("bad")
    conn = cache._conn()
    conn.execute(
        "INSERT OR REPLACE INTO query_cache(key, payload, created_at, expires_at) VALUES(?,?,?,?)",
        (key, "{not json", time.time(), time.time() + 3600),
    )
    conn.commit()
    assert cache.get(key) is None