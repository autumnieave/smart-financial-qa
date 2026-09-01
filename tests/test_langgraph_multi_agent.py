"""agents.langgraph_multi_agent（LangGraph 多 Agent 协作，实验）单元测试。

全部离线：fake OpenAI 客户端 + stub 工具，不依赖 Qdrant/MySQL/LLM 等外部服务。
"""

import json
import types
from typing import Any, Dict, List, Optional

from agents.langgraph_multi_agent import LangGraphMultiAgentPlanner


class FakeMessage:
    def __init__(self, content: str):
        self.content = content


class FakeClient:
    """伪造 OpenAI 兼容客户端：按顺序弹出预设回复，记录每次请求。"""

    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        ns = types.SimpleNamespace()
        ns.create = self._create
        self.chat = types.SimpleNamespace(completions=ns)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        msg = FakeMessage(self._responses.pop(0))
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class StubRag:
    """stub RAGPipeline：记录 search_reports 查询，返回固定引用结果。"""

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
    financial_tool: Optional[Any] = None,
    research_tool: Optional[Any] = None,
) -> LangGraphMultiAgentPlanner:
    config = types.SimpleNamespace(
        LLM_MODEL="test-model",
        AGENT_ENABLE_THINKING=False,
        AGENT_LANGGRAPH_CHECKPOINT=False,
        AGENT_LANGGRAPH_MAX_HISTORY=40,
        CONVERSATION_TIMEOUT_SECONDS=1800,
    )
    return LangGraphMultiAgentPlanner(
        llm_client=client,
        config=config,
        rag_pipeline=rag,
        financial_tool=financial_tool,
        research_tool=research_tool,
    )


def _fin_tool(query: str, user_id: str) -> str:
    return json.dumps({"content": f"财务结果：{query}", "image": ["/result/fin.png"]}, ensure_ascii=False)


def test_graph_builds():
    """图可编译，节点齐全（离线，不触发模型调用）。"""
    planner = _make_planner(FakeClient([]), StubRag())
    nodes = set(planner._graph.get_graph().nodes)
    assert {"supervisor", "tools", "aggregator", "finalize"} <= nodes


def test_supervisor_splits_and_aggregates():
    """拆 2 个子任务（财务+研报）→ 子 Agent 执行 → 汇总，图片/引用被合并。"""
    rag = StubRag()
    fin_calls: List[str] = []
    client = FakeClient([
        json.dumps({
            "tasks": [
                {"agent": "financial", "query": "万邦德2023营收"},
                {"agent": "research", "query": "万邦德研报观点"},
            ],
            "direct_answer": None,
        }, ensure_ascii=False),
        json.dumps({
            "content": "整合回答",
            "image": ["/result/fin.png"],
            "references": [{"paper_path": "stub.md"}],
        }, ensure_ascii=False),
    ])

    def fin_tool(query: str, user_id: str) -> str:
        fin_calls.append(query)
        return _fin_tool(query, user_id)

    result = _make_planner(client, rag, financial_tool=fin_tool).execute("分析万邦德")
    assert result["content"] == "整合回答"
    assert result["image"] == ["/result/fin.png"]
    assert result["references"] == [{"paper_path": "stub.md"}]
    assert fin_calls == ["万邦德2023营收"]
    assert rag.calls == ["万邦德研报观点"]
    # supervisor 请求带 system + 用户问题；aggregator 请求带子结果上下文
    assert client.requests[0]["messages"][-1]["content"] == "分析万邦德"
    assert "财务结果" in json.dumps(client.requests[1]["messages"], ensure_ascii=False)


def test_no_task_direct_answer():
    """supervisor 未拆出任务时，直接以 direct_answer 回答。"""
    client = FakeClient([
        json.dumps({"tasks": [], "direct_answer": "你好，我可以帮你查财务数据和研报。"}, ensure_ascii=False),
    ])
    result = _make_planner(client, StubRag()).execute("你好")
    assert result["content"] == "你好，我可以帮你查财务数据和研报。"


def test_supervisor_invalid_json_fallback():
    """supervisor 输出非法 JSON → 空任务 → finalize 兜底为文本回答。"""
    client = FakeClient(["抱歉，我无法解析"])
    result = _make_planner(client, StubRag()).execute("测试")
    assert result["content"] == "抱歉，我无法解析"


def test_aggregator_merges_missing_references():
    """aggregator 输出缺 references 时，代码兜底合并研报引用。"""
    rag = StubRag()
    client = FakeClient([
        json.dumps({"tasks": [{"agent": "research", "query": "有什么观点"}], "direct_answer": None}, ensure_ascii=False),
        json.dumps({"content": "整合回答", "image": [], "references": []}, ensure_ascii=False),
    ])
    result = _make_planner(client, rag).execute("有什么观点")
    assert any(r["paper_path"] == "stub.md" for r in result["references"])
    assert "研报检索结果" in result["content"] or result["content"] == "整合回答"


def test_thinking_disabled_by_default():
    """多 Agent 循环默认关闭思考模式（AGENT_ENABLE_THINKING=False）。"""
    client = FakeClient([
        json.dumps({"tasks": [], "direct_answer": "ok"}, ensure_ascii=False),
    ])
    _make_planner(client, StubRag()).execute("测试")
    assert client.requests[0]["extra_body"]["enable_thinking"] is False
