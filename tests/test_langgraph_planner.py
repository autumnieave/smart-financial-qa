"""agents.langgraph_planner（LangGraph 版 Agent，实验）单元测试。

全部离线：fake OpenAI 客户端 + stub RAG，不依赖 Qdrant/LLM 等外部服务。
"""

import json
import types
from typing import Any, Dict, List, Optional

from agents.langgraph_planner import LangGraphPlanner, MAX_ROUNDS


class FakeToolCall:
    def __init__(self, name: str, arguments: str, call_id: str = "tc1"):
        self.id = call_id
        self.function = types.SimpleNamespace(name=name, arguments=arguments)


class FakeMessage:
    def __init__(self, content: Optional[str], tool_calls: Optional[List[FakeToolCall]] = None):
        self.content = content
        self.tool_calls = tool_calls


class FakeClient:
    """伪造 OpenAI 兼容客户端：按顺序弹出预设回复，记录每次请求。"""

    def __init__(self, responses: List[FakeMessage]):
        self._responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        ns = types.SimpleNamespace()
        ns.create = self._create
        self.chat = types.SimpleNamespace(completions=ns)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        msg = self._responses.pop(0)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class StubRag:
    """stub RAGPipeline：记录 search_reports 查询，返回固定结果。"""

    def __init__(self):
        self.calls: List[str] = []
        self.conversation_state = types.SimpleNamespace(sql="")

    def query(self, question: str, verbose: bool = False) -> Dict[str, Any]:
        self.calls.append(question)
        return {
            "content": f"研报检索结果：{question}",
            "image": [],
            "references": [{"paper_path": "stub.md", "text": "stub", "paper_image": ""}],
        }


def _make_planner(
    client: FakeClient,
    rag: StubRag,
    checkpoint: Optional[str] = None,
    db_path: Optional[str] = None,
) -> LangGraphPlanner:
    config = types.SimpleNamespace(
        LLM_MODEL="test-model",
        AGENT_ENABLE_THINKING=False,
        AGENT_LANGGRAPH_CHECKPOINT=checkpoint is not None,
        AGENT_LANGGRAPH_CHECKPOINT_BACKEND=checkpoint or "none",
        AGENT_LANGGRAPH_CHECKPOINT_PATH=db_path or "database/langgraph_checkpoints.sqlite",
        AGENT_LANGGRAPH_MAX_HISTORY=40,
        CONVERSATION_TIMEOUT_SECONDS=1800,
    )
    return LangGraphPlanner(llm_client=client, config=config, rag_pipeline=rag)


def _tool_call(name: str, query: str) -> FakeMessage:
    return FakeMessage(
        content=None,
        tool_calls=[FakeToolCall(name, json.dumps({"query": query}, ensure_ascii=False))],
    )


def test_graph_builds():
    """图可编译，节点齐全（离线，不触发模型调用）。"""
    planner = _make_planner(FakeClient([]), StubRag())
    graph = planner._graph.get_graph()
    nodes = set(graph.nodes)
    assert {"call_model", "tools", "finalize"} <= nodes


def test_single_tool_round():
    """一轮工具调用：search_reports 执行并把结果回填，最终 JSON 被解析。"""
    rag = StubRag()
    client = FakeClient([
        _tool_call("search_reports", "贵州茅台2024营收"),
        FakeMessage(json.dumps({"content": "答案", "image": [], "references": []}, ensure_ascii=False)),
    ])
    result = _make_planner(client, rag).execute("贵州茅台2024营收多少")
    assert result == {"content": "答案", "image": [], "references": []}
    assert rag.calls == ["贵州茅台2024营收"]
    # 工具结果确实回填给了模型（第二轮请求含 tool 消息）
    assert client.requests[1]["messages"][-1]["role"] == "tool"


def test_no_tool_direct_json():
    """模型直接返回 JSON（无工具调用）时原样解析。"""
    client = FakeClient([FakeMessage('{"content": "直接回答", "image": [], "references": []}')])
    result = _make_planner(client, StubRag()).execute("1+1")
    assert result["content"] == "直接回答"


def test_invalid_json_wrapped():
    """非 JSON 输出兜底为 {content, image, references} 结构。"""
    client = FakeClient([FakeMessage("抱歉，我无法回答")])
    result = _make_planner(client, StubRag()).execute("测试")
    assert result["content"] == "抱歉，我无法回答"
    assert result["image"] == [] and result["references"] == []


def test_max_rounds_timeout():
    """模型持续要求工具调用时，达到 MAX_ROUNDS 后返回超时兜底。"""
    rag = StubRag()
    client = FakeClient([_tool_call("search_reports", f"q{i}") for i in range(MAX_ROUNDS)])
    result = _make_planner(client, rag).execute("循环问题")
    assert "超时" in result["content"]
    assert len(rag.calls) == MAX_ROUNDS


def test_history_trimmed_to_12():
    """多轮历史超过 12 条时只携带最近 12 条进入模型。"""
    history = [{"role": "user", "content": f"h{i}"} for i in range(15)]
    client = FakeClient([FakeMessage('{"content": "ok", "image": [], "references": []}')])
    _make_planner(client, StubRag()).execute("新问题", history=history)
    sent = client.requests[0]["messages"]
    assert sent[0]["role"] == "system"
    assert sent[-1] == {"role": "user", "content": "新问题"}
    user_msgs = [m for m in sent if m["role"] == "user"]
    assert len(user_msgs) == 13  # 12 条历史 + 当前问题
    assert user_msgs[0]["content"] == "h3"


def test_thinking_disabled_by_default():
    """Agent 循环默认关闭思考模式（AGENT_ENABLE_THINKING=False → extra_body.enable_thinking=False）。"""
    client = FakeClient([FakeMessage('{"content": "ok", "image": [], "references": []}')])
    _make_planner(client, StubRag()).execute("测试")
    assert client.requests[0]["extra_body"]["enable_thinking"] is False


# ── Checkpoint 会话记忆（thread_id=user_id）─────────────────────────────

def test_checkpoint_same_user_recalls_history():
    """同 user_id 二次执行：checkpoint 记忆使第二轮请求携带第一轮对话。"""
    rag = StubRag()
    client1 = FakeClient([FakeMessage('{"content": "第一轮回答", "image": [], "references": []}')])
    planner = _make_planner(client1, rag, checkpoint="memory")
    planner.execute("第一问", user_id="u1")

    client2 = FakeClient([FakeMessage('{"content": "第二轮回答", "image": [], "references": []}')])
    planner.client = client2
    planner.execute("第二问", user_id="u1")

    sent = client2.requests[0]["messages"]
    joined = "".join(str(m.get("content", "")) for m in sent)
    assert "第一问" in joined
    assert "第一轮回答" in joined
    assert sent[-1]["content"] == "第二问"


def test_checkpoint_user_isolation():
    """不同 user_id 互不串话（thread_id=user_id 隔离）。"""
    rag = StubRag()
    planner = _make_planner(
        FakeClient([FakeMessage('{"content": "甲的答案", "image": [], "references": []}')]),
        rag,
        checkpoint="memory",
    )
    planner.execute("甲的问题", user_id="user-a")

    client_b = FakeClient([FakeMessage('{"content": "乙的答案", "image": [], "references": []}')])
    planner.client = client_b
    planner.execute("乙的问题", user_id="user-b")

    sent = client_b.requests[0]["messages"]
    joined = "".join(str(m.get("content", "")) for m in sent)
    assert "甲的问题" not in joined
    assert "乙的问题" in joined


def test_checkpoint_timeout_resets():
    """超过会话超时后 checkpoint 历史不再携带（视为新话题）。"""
    rag = StubRag()
    planner = _make_planner(
        FakeClient([FakeMessage('{"content": "旧答案", "image": [], "references": []}')]),
        rag,
        checkpoint="memory",
    )
    planner.execute("旧问题", user_id="u1")
    # 把该 thread 的活跃时间改为很久以前，模拟会话超时
    planner._graph.update_state({"configurable": {"thread_id": "u1"}}, {"last_active": 0})

    client2 = FakeClient([FakeMessage('{"content": "新答案", "image": [], "references": []}')])
    planner.client = client2
    planner.execute("新问题", user_id="u1")

    sent = client2.requests[0]["messages"]
    joined = "".join(str(m.get("content", "")) for m in sent)
    assert "旧问题" not in joined
    assert sent[-1]["content"] == "新问题"


def test_checkpoint_sqlite_persists_across_instances(tmp_path):
    """sqlite 后端：跨实例（模拟重启）仍能按 user_id 恢复历史。"""
    from pathlib import Path

    db = str(tmp_path / "ckpt.sqlite")
    rag = StubRag()
    planner1 = _make_planner(
        FakeClient([FakeMessage('{"content": "a1", "image": [], "references": []}')]),
        rag,
        checkpoint="sqlite",
        db_path=db,
    )
    planner1.execute("重启前的问题", user_id="u1")
    planner1.close()

    client2 = FakeClient([FakeMessage('{"content": "a2", "image": [], "references": []}')])
    planner2 = _make_planner(client2, rag, checkpoint="sqlite", db_path=db)
    planner2.execute("重启后的问题", user_id="u1")
    planner2.close()

    assert Path(db).exists()
    sent = client2.requests[0]["messages"]
    joined = "".join(str(m.get("content", "")) for m in sent)
    assert "重启前的问题" in joined
    assert "重启后的问题" in sent[-1]["content"]
