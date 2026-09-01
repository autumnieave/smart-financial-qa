"""RAGPipeline 记忆持久化集成测试（__new__ 绕过外部依赖，不连 Qdrant/MySQL）。"""

from types import SimpleNamespace

from memory.conversation import ConversationState
from pipelines.rag_pipeline import RAGPipeline


def _pipeline(tmp_path):
    """构造最小 RAGPipeline 桩：只带记忆持久化所需属性"""
    p = RAGPipeline.__new__(RAGPipeline)
    from memory.store import SQLiteMemoryStore

    p.config = SimpleNamespace(MEMORY_TTL_SECONDS=3600)
    p.memory_store = SQLiteMemoryStore(db_path=str(tmp_path / "mem.db"), ttl=3600)
    p.conversation_state = ConversationState()
    return p


def test_load_save_roundtrip(tmp_path):
    p = _pipeline(tmp_path)
    state = ConversationState(user_id="u1", sql="SELECT 1", history=[{"role": "user", "content": "Q"}])
    p._save_conversation(state)
    loaded = p._load_conversation("u1")
    assert loaded.user_id == "u1" and loaded.sql == "SELECT 1"
    assert loaded.history == [{"role": "user", "content": "Q"}]


def test_load_missing_returns_fresh(tmp_path):
    p = _pipeline(tmp_path)
    st = p._load_conversation("ghost")
    assert st.user_id == "ghost" and st.sql == "" and st.history == []


def test_multi_user_isolation(tmp_path):
    p = _pipeline(tmp_path)
    p._save_conversation(ConversationState(user_id="a", sql="SELECT A"))
    p._save_conversation(ConversationState(user_id="b", sql="SELECT B"))
    assert p._load_conversation("a").sql == "SELECT A"
    assert p._load_conversation("b").sql == "SELECT B"


def test_reset_conversation(tmp_path):
    p = _pipeline(tmp_path)
    p.conversation_state = ConversationState(user_id="u1", sql="SELECT 1")
    p._save_conversation(p.conversation_state)
    p.reset_conversation("u1")
    st = p._load_conversation("u1")
    assert st.sql == "" and st.history == []
    assert p.conversation_state.user_id == "u1"


def test_no_store_degrades_to_memory(tmp_path):
    p = RAGPipeline.__new__(RAGPipeline)
    p.config = SimpleNamespace(MEMORY_TTL_SECONDS=3600)
    p.memory_store = None
    p.conversation_state = ConversationState()
    st = p._load_conversation("x")  # 无存储不报错
    assert st.user_id == "x"
    p._save_conversation(st)  # 无存储静默跳过
