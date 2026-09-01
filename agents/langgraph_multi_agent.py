"""
agents/langgraph_multi_agent.py
LangGraph 多 Agent 协作规划器（实验）—— supervisor-workers 模式。

与 agents/langgraph_planner.py::LangGraphPlanner 同接口（execute 契约）、同 checkpoint 机制，
但把"单 Agent 多工具循环"升级为"规划器拆任务 → 财务/研报子 Agent 执行 → 汇总成报告"：
- supervisor：LLM 拆解子任务（financial / research）
- tools：按任务类型调用财务查询（原生 SQL 链路）或研报检索（RAG）
- aggregator：把子结果整合成最终 {content, image, references}

仅 AGENT_PLANNER_BACKEND=langgraph 且 AGENT_LANGGRAPH_MULTI_AGENT=true 时启用（实验）；
生产默认仍为自研 AgentPlanner（handwritten）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import END, START, StateGraph

from prompts.multi_agent import MULTI_AGENT_AGGREGATOR_PROMPT, MULTI_AGENT_SUPERVISOR_PROMPT

logger = logging.getLogger(__name__)


class MultiAgentState(TypedDict, total=False):
    """多 Agent 协作状态：任务列表 / 子结果 / 最终结果。"""

    messages: List[Dict[str, Any]]
    user_query: str
    tasks: List[Dict[str, str]]
    subtask_results: Dict[str, List[Dict[str, Any]]]
    rounds: int
    user_id: str
    result: Optional[Dict[str, Any]]
    last_active: float  # checkpoint 记忆新鲜度（超时视为新话题）


class LangGraphMultiAgentPlanner:
    """LangGraph 多 Agent 协作规划器（supervisor-workers，实验）。

    Attributes:
        client: OpenAI 兼容客户端（LLMGenerator.client，须含 chat.completions.create）
        config: RAGConfig（LLM_MODEL / AGENT_ENABLE_THINKING / checkpoint 配置）
        rag: RAGPipeline（研报检索入口）
        financial_tool: 财务查询可调用（默认 call_financial_chatflow，测试可注入 stub）
        research_tool: 研报检索可调用（默认 rag.query，测试可注入 stub）
    """

    def __init__(
        self,
        llm_client: Any,
        config: Any,
        rag_pipeline: Any,
        financial_tool: Optional[Callable[[str, str], Any]] = None,
        research_tool: Optional[Callable[[str], Dict[str, Any]]] = None,
    ) -> None:
        self.client = llm_client
        self.config = config
        self.rag = rag_pipeline
        self._financial_tool = financial_tool
        self._research_tool = research_tool
        self._sqlite_conn = None
        self._checkpointer = self._build_checkpointer()
        self._graph = self._build_graph()

    # ── Checkpoint（与 LangGraphPlanner 同一契约）───────────────────────
    def _build_checkpointer(self) -> Any:
        """按配置构建 LangGraph checkpointer：sqlite=落盘（默认）/ memory=进程内存 / none=关闭。

        sqlite 后端依赖 langgraph-checkpoint-sqlite 包；初始化失败降级为内存，保证主流程可用。
        """
        if not getattr(self.config, "AGENT_LANGGRAPH_CHECKPOINT", True):
            return None
        backend = str(getattr(self.config, "AGENT_LANGGRAPH_CHECKPOINT_BACKEND", "sqlite")).strip().lower()
        if backend == "sqlite":
            try:
                import sqlite3  # noqa: PLC0415
                from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415

                path = str(getattr(self.config, "AGENT_LANGGRAPH_CHECKPOINT_PATH", "database/langgraph_checkpoints.sqlite"))
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(path, check_same_thread=False)
                self._sqlite_conn = conn
                logger.info("LangGraph 多 Agent checkpoint 已启用（sqlite: %s）", path)
                return SqliteSaver(conn)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LangGraph sqlite checkpoint 初始化失败，降级为内存: %s", exc)
        if backend == "none":
            return None
        from langgraph.checkpoint.memory import MemorySaver  # noqa: PLC0415

        logger.info("LangGraph 多 Agent checkpoint 已启用（memory）")
        return MemorySaver()

    def close(self) -> None:
        """释放 sqlite checkpoint 连接（进程退出/测试清理时调用）。"""
        conn = getattr(self, "_sqlite_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._sqlite_conn = None

    # ── 图构建 ─────────────────────────────────────────────────────────
    def _build_graph(self) -> Any:
        """构建 supervisor-workers 状态机：supervisor → (tools) → aggregator / finalize。"""
        graph = StateGraph(MultiAgentState)
        graph.add_node("supervisor", self._supervisor)
        graph.add_node("tools", self._run_subtasks)
        graph.add_node("direct", self._direct)
        graph.add_node("aggregator", self._aggregate)
        graph.add_node("finalize", self._finalize)
        graph.add_edge(START, "supervisor")
        graph.add_conditional_edges(
            "supervisor",
            self._route_after_supervisor,
            {"tools": "tools", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "tools",
            self._route_after_tools,
            {"direct": "direct", "aggregator": "aggregator"},
        )
        graph.add_edge("direct", END)
        graph.add_edge("aggregator", END)
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=self._checkpointer)

    # ── 节点 ───────────────────────────────────────────────────────────
    def _supervisor(self, state: MultiAgentState) -> Dict[str, Any]:
        """规划节点：LLM 拆解子任务（financial / research）。"""
        self._emit("parse")
        sup_model = getattr(self.config, "SUPERVISOR_MODEL", "") or None
        content = self._call_llm(state["messages"], max_tokens=500, model=sup_model)
        tasks, _direct = self._parse_tasks(content)
        # 快速模型拆任务失败（非法 JSON）时，用主模型重试一次兜底，避免把原始输出当答案
        if sup_model and not tasks and not self._is_valid_json(content):
            logger.info("supervisor 快速模型输出非法 JSON，回退主模型重试")
            content = self._call_llm(state["messages"], max_tokens=500)
            tasks, _direct = self._parse_tasks(content)
        return {
            "tasks": tasks,
            "messages": state["messages"] + [{"role": "assistant", "content": content}],
        }

    @staticmethod
    def _is_valid_json(content: str) -> bool:
        """判断 supervisor 输出是否为合法 JSON（快速模型兜底用）。"""
        try:
            json.loads(content or "")
            return True
        except (TypeError, ValueError):
            return False

    def _run_subtasks(self, state: MultiAgentState) -> Dict[str, Any]:
        """工具节点：按任务类型调用财务查询 / 研报检索，结果写入 subtask_results。

        路线 1：多任务时并行执行 financial / research 子任务（AGENT_PARALLEL_TOOLS=false 可关），
        串行最坏情况是两者耗时相加；并行后耗时取两者最大值。
        """
        results: Dict[str, List[Dict[str, Any]]] = dict(state.get("subtask_results") or {})
        user_id = state.get("user_id", "default")
        tasks = [t for t in (state.get("tasks") or []) if (t.get("query") or "").strip()]
        if not tasks:
            return {"subtask_results": results, "rounds": int(state.get("rounds", 0)) + 1}

        def _exec(task: Dict[str, str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
            agent = task.get("agent")
            query = (task.get("query") or "").strip()
            if agent == "financial":
                self._emit("query_financial")
                return ("financial", {"query": query, "raw": self._run_financial(query, user_id)})
            if agent == "research":
                self._emit("search_reports")
                return ("research", {"query": query, "raw": self._run_research(query)})
            return (None, None)

        parallel = getattr(self.config, "AGENT_PARALLEL_TOOLS", True)
        if parallel and len(tasks) > 1:
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as executor:
                outcomes = list(executor.map(_exec, tasks))
        else:
            outcomes = [_exec(t) for t in tasks]
        for agent, item in outcomes:
            if agent and item is not None:
                results.setdefault(agent, []).append(item)
        return {"subtask_results": results, "rounds": int(state.get("rounds", 0)) + 1}

    def _direct(self, state: MultiAgentState) -> Dict[str, Any]:
        """单任务直出节点：supervisor 只拆出 1 个财务/研报任务时，直接透传子 Agent 结果作为最终答案（省 1 次汇总 LLM）。"""
        results = state.get("subtask_results") or {}
        result: Dict[str, Any] = {"content": "", "image": [], "references": []}
        found = False
        for agent in ("financial", "research"):
            items = results.get(agent) or []
            if not items:
                continue
            raw = items[0].get("raw")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = None
            if not isinstance(raw, dict):
                continue
            found = True
            result["content"] = str(raw.get("content") or result.get("content") or "")
            if agent == "financial" and raw.get("chart_json") is not None:
                result["chart_json"] = raw["chart_json"]
            for img in raw.get("image") or []:
                if img not in result["image"]:
                    result["image"].append(img)
            for ref in raw.get("references") or []:
                if isinstance(ref, dict) and ref not in result["references"]:
                    result["references"].append(ref)
        if not found:
            return {"result": {"content": "抱歉，未能完成查询。", "image": [], "references": []}}
        return {"result": result}

    def _aggregate(self, state: MultiAgentState) -> Dict[str, Any]:
        """汇总节点：把财务/研报子结果交给 LLM 整合成最终答案。"""
        self._emit("generate")
        user_prompt = self._build_context(state)
        agg_messages = [
            {"role": "system", "content": MULTI_AGENT_AGGREGATOR_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        agg_model = getattr(self.config, "AGGREGATOR_MODEL", "") or self.config.LLM_MODEL
        try:
            content = self._call_llm(agg_messages, max_tokens=1800, model=agg_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("多 Agent 汇总：%s 调用失败，回退主模型: %s", agg_model, exc)
            content = self._call_llm(agg_messages, max_tokens=1800)
        result = self._parse_json_loose(content)
        result = self._merge_results(result, state.get("subtask_results") or {})
        return {"result": result}

    def _finalize(self, state: MultiAgentState) -> Dict[str, Any]:
        """收尾节点：supervisor 未拆出任务时，以其回复作为答案（优先 direct_answer）。"""
        self._emit("generate")
        content = (state["messages"][-1].get("content") or "").strip() if state.get("messages") else ""
        if not content:
            return {"result": {"content": "抱歉，无法理解该问题，请补充条件后重试。", "image": [], "references": []}}
        try:
            obj = json.loads(content)
            if isinstance(obj, dict) and not obj.get("tasks") and obj.get("direct_answer"):
                return {"result": {"content": str(obj["direct_answer"]), "image": [], "references": []}}
        except (TypeError, ValueError):
            pass
        return {"result": self._parse_json_loose(content)}

    # ── 路由 ───────────────────────────────────────────────────────────
    def _route_after_tools(self, state: MultiAgentState) -> str:
        """工具执行后路由：单任务且开启直出时走 direct，否则走 aggregator 汇总。"""
        if not getattr(self.config, "AGENT_MULTI_DIRECT_RESULT", True):
            return "aggregator"
        tasks = [t for t in (state.get("tasks") or []) if (t.get("query") or "").strip()]
        return "direct" if len(tasks) == 1 else "aggregator"

    def _route_after_supervisor(self, state: MultiAgentState) -> str:
        """拆出任务则进 tools，否则 finalize 直接回答。"""
        return "tools" if state.get("tasks") else "finalize"

    # ── 工具执行 ───────────────────────────────────────────────────────
    def _run_financial(self, query: str, user_id: str) -> Any:
        """财务子 Agent：默认走原生财务链路，测试可注入 stub。"""
        if self._financial_tool is not None:
            return self._financial_tool(query, user_id)
        from agents.planner import call_financial_chatflow  # noqa: PLC0415

        return call_financial_chatflow(self.rag, query, user_id=user_id)

    def _run_research(self, query: str) -> Any:
        """研报子 Agent：默认走 RAG 检索（含引用），测试可注入 stub。"""
        if self._research_tool is not None:
            return self._research_tool(query)
        cb = getattr(self, "on_chunk", None)
        if cb is not None:
            return self.rag.query(query, verbose=False, stream_callback=cb)
        return self.rag.query(query, verbose=False)

    # ── LLM 与解析 ─────────────────────────────────────────────────────
    def _call_llm(self, messages: List[Dict[str, Any]], max_tokens: Optional[int] = None, model: Optional[str] = None) -> str:
        """统一 LLM 调用（非流式；qwen3.5-plus 默认关闭思考模式）。"""
        response = self.client.chat.completions.create(
            model=model or self.config.LLM_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            extra_body={"enable_thinking": getattr(self.config, "AGENT_ENABLE_THINKING", False)},
        )
        return (response.choices[0].message.content or "").strip()

    def _parse_tasks(self, content: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
        """解析 supervisor 输出任务列表；非法 JSON 时退回空任务（走 finalize 兜底）。"""
        try:
            obj = json.loads(content)
        except (TypeError, ValueError):
            return [], content
        tasks: List[Dict[str, str]] = []
        if isinstance(obj, dict):
            raw_tasks = obj.get("tasks") or []
            for t in raw_tasks:
                if isinstance(t, dict) and t.get("agent") in ("financial", "research") and (t.get("query") or "").strip():
                    tasks.append({"agent": str(t["agent"]), "query": str(t["query"]).strip()})
            return tasks, obj.get("direct_answer")
        if isinstance(obj, list):
            for t in obj:
                if isinstance(t, dict) and t.get("agent") in ("financial", "research") and (t.get("query") or "").strip():
                    tasks.append({"agent": str(t["agent"]), "query": str(t["query"]).strip()})
            return tasks, None
        return [], content

    def _parse_json_loose(self, content: str) -> Dict[str, Any]:
        """解析最终答案 JSON；非法 JSON 时兜底为文本回答。"""
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                return obj
        except (TypeError, ValueError):
            pass
        return {"content": content, "image": [], "references": []}

    def _build_context(self, state: MultiAgentState) -> str:
        """把子 Agent 结果拼成汇总 prompt 的输入。"""
        parts = [f"用户问题：{state.get('user_query', '')}\n"]
        results = state.get("subtask_results") or {}
        for item in results.get("financial") or []:
            parts.append("[财务数据结果]\n")
            parts.append(f"查询：{item.get('query')}\n")
            raw = item.get("raw")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = None
            if isinstance(raw, dict):
                raw = {k: v for k, v in raw.items() if k != "chart_json"}
                raw = json.dumps(raw, ensure_ascii=False)
            parts.append(f"结果：{raw}\n")
        for item in results.get("research") or []:
            parts.append("[研报检索结果]\n")
            parts.append(f"查询：{item.get('query')}\n")
            raw = item.get("raw")
            if isinstance(raw, dict):
                raw = raw.get("content") or ""
            raw = str(raw or "")[:2500]
            parts.append(f"结果：{raw}\n")
        return "\n".join(parts)

    def _merge_results(self, result: Dict[str, Any], subtask_results: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """兜底合并：把子 Agent 的图片/引用补进最终结果（防 LLM 遗漏）。"""
        result = dict(result or {})
        images = list(result.get("image") or [])
        refs = list(result.get("references") or [])
        chart = result.get("chart_json")
        for item in subtask_results.get("financial") or []:
            raw = item.get("raw")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (TypeError, ValueError):
                    raw = {}
            raw = raw or {}
            if chart is None:
                chart = raw.get("chart_json")
            for img in raw.get("image") or []:
                if img and img not in images:
                    images.append(img)
        existing_paths = {r.get("paper_path") for r in refs if isinstance(r, dict)}
        for item in subtask_results.get("research") or []:
            raw = item.get("raw") or {}
            for r in raw.get("references") or []:
                if isinstance(r, dict) and r.get("paper_path") not in existing_paths:
                    refs.append(r)
                    existing_paths.add(r.get("paper_path"))
        result["image"] = images
        result["references"] = refs
        if chart is not None:
            result["chart_json"] = chart
        return result

    def _emit(self, stage: str) -> None:
        """发射前端阶段事件（stage 回调，未注入时忽略）。"""
        cb = getattr(self, "on_stage", None)
        if cb:
            cb(stage)

    # ── Checkpoint 记忆（与 LangGraphPlanner 同一契约）──────────────────
    def _load_memory(self, history: Optional[List[Dict[str, Any]]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """从 checkpoint 读回该 user_id 上次会话；超时/不存在则退回传入 history。"""
        if self._checkpointer is None:
            return list(history or [])
        try:
            snap = self._graph.get_state(config)
            values = (snap.values or {}) if snap else {}
            msgs = values.get("messages")
            if not msgs:
                return list(history or [])
            last_active = values.get("last_active") or 0
            timeout = int(getattr(self.config, "CONVERSATION_TIMEOUT_SECONDS", 1800))
            if time.time() - last_active > timeout:
                return []  # 超时视为新话题，不携带历史
            return self._trim_messages(msgs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangGraph 多 Agent checkpoint 读取失败，退回传入历史: %s", exc)
            return list(history or [])

    def _trim_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """截断历史消息（保留首条 system），避免截断后首条为 tool 导致引用悬空。"""
        max_len = int(getattr(self.config, "AGENT_LANGGRAPH_MAX_HISTORY", 40))
        msgs = list(messages or [])
        if len(msgs) <= max_len:
            return msgs
        head = msgs[:1] if msgs and (msgs[0] or {}).get("role") == "system" else []
        body = msgs[len(head):]
        keep = body[-(max_len - len(head)):]
        while keep and keep[0].get("role") == "tool":
            idx = len(body) - len(keep) - 1
            if idx < 0:
                break
            keep.insert(0, body[idx])
        return head + keep

    # ── 入口 ───────────────────────────────────────────────────────────
    def execute(
        self,
        user_query: str,
        history: Optional[List[Dict[str, Any]]] = None,
        user_id: str = "default",
        verbose: bool = True,
        on_stage: Optional[Callable[[str], None]] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """执行多 Agent 任务，返回 {content, image, references}（与 AgentPlanner 一致）。

        Args:
            user_query: 用户问题
            history: 多轮历史（取最近 12 条；checkpoint 可用时被忽略）
            user_id: 会话用户标识（thread_id，隔离各会话记忆）
            verbose: 是否打印工具执行日志
            on_stage: 前端阶段事件回调（parse / query_financial / search_reports / generate）

        Returns:
            最终结果字典
        """
        config = {"configurable": {"thread_id": user_id}}
        base = self._load_memory(history, config)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": MULTI_AGENT_SUPERVISOR_PROMPT}]
        if base:
            # checkpoint 历史已含 system 提示词则直接续用；传入 history 保持原 12 条截断口径
            if (base[0] or {}).get("role") == "system":
                messages = list(base)
            else:
                messages.extend(base[-12:])
        messages.append({"role": "user", "content": user_query})
        self.verbose = verbose
        self.on_stage = on_stage
        self.on_chunk = on_chunk
        # 注意：parse 事件由 _supervisor 节点 emit，这里不再重复发送
        output = self._graph.invoke(
            {
                "messages": messages,
                "user_query": user_query,
                "tasks": [],
                "subtask_results": {},
                "rounds": 0,
                "user_id": user_id,
                "result": None,
            },
            config,
        )
        # 记录本轮活跃时间，供下次 execute 判断 checkpoint 是否超时
        if self._checkpointer is not None:
            try:
                self._graph.update_state(config, {"last_active": time.time()})
            except Exception as exc:  # noqa: BLE001
                logger.warning("LangGraph 多 Agent checkpoint 写回 last_active 失败: %s", exc)
        return output["result"]
