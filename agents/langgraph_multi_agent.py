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
from prompts.fallback import build_refuse_not_understood, log_fallback as _log_fallback
from utils.output_contracts import (
    get_stats as _contract_stats,
    validate_aggregate_result as _contract_aggregate,
    validate_supervisor_output as _contract_supervisor,
)

logger = logging.getLogger(__name__)

#: B-26 财务查询意图关键词（supervisor 空任务兜底路由用；粗粒度宁多勿漏——查无数据由财务侧标准拒答兜底）
_FINANCIAL_INTENT_MARKERS: tuple = (
    "营收", "营业收入", "收入", "净利润", "净利", "利润总额", "利润",
    "研发费用", "毛利率", "净利率", "每股", "EPS", "净资产", "ROE",
    "资产负债率", "负债", "现金流", "货币资金", "存货", "应收账款",
    "市盈率", "市净率", "市值", "股价", "估值",
    "同比", "环比", "增长", "下降", "排名", "最高", "最低",
    "季度", "三季度", "年报", "半年报", "三季报", "财报", "业绩",
)


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
        # B-26 兜底：supervisor 仍无任务但问题带财务意图 → 强制补派 financial 单任务，
        # 先由财务子 Agent 查库核验，避免未经查库就 finalize 断言“数据未披露/不存在”。
        tasks = self._ensure_financial_task(state["user_query"], tasks)
        return {
            "tasks": tasks,
            "messages": state["messages"] + [{"role": "assistant", "content": content}],
        }

    @staticmethod
    def _looks_like_financial_query(question: str) -> bool:
        """粗判问题是否带财务数据查询意图（B-26 兜底路由用）。

        只判断“是否值得让财务子 Agent 查库一次”，不做精确语义识别；
        关键词命中可能误报，由财务侧查库后的标准拒答话术兜底。

        Args:
            question: 用户原始问题

        Returns:
            True=带财务意图关键词（指标/期间/对比/公司财务类）
        """
        text = (question or "").strip()
        if not text:
            return False
        return any(k in text for k in _FINANCIAL_INTENT_MARKERS)

    @staticmethod
    def _ensure_financial_task(user_query: str, tasks: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """supervisor 未拆任务且问题带财务意图时，强制补派一个 financial 单任务（B-26）。

        背景：supervisor 对“注入包装 + 库内财务子问题”可能误判为无任务并直接 finalize
        拒答（如 C2001 “数据未披露”式误拒答，理由与库内事实不符）。本兜底把原始问题交给
        财务子 Agent 查库核验：可查则返回库内真实数据，查无则由财务侧输出标准拒答话术。

        Args:
            user_query: 用户原始问题
            tasks: supervisor 已拆出的任务列表（可能为空）

        Returns:
            补派后的任务列表（已有任务/无财务意图时原样返回）
        """
        tasks = list(tasks or [])
        if tasks or not LangGraphMultiAgentPlanner._looks_like_financial_query(user_query):
            return tasks
        return [{"agent": "financial", "query": user_query}]

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
        # B-17 契约闸门：缺 content 等非法汇总结果拒绝透传，替换为兜底文案并记录格式事件
        agg_res = _contract_aggregate(result)
        if not agg_res.ok:
            _contract_stats().record("aggregate_result", False, agg_res.errors)
            result = {
                "content": "抱歉，未能生成结构化答案，请补充条件或稍后重试。",
                "image": result.get("image") or [],
                "references": result.get("references") or [],
            }
        else:
            _contract_stats().record("aggregate_result", True, [])
        return {"result": result}

    def _finalize(self, state: MultiAgentState) -> Dict[str, Any]:
        """收尾节点：supervisor 未拆出任务时，以其回复作为答案（优先 direct_answer）。"""
        self._emit("generate")
        content = (state["messages"][-1].get("content") or "").strip() if state.get("messages") else ""
        if not content:
            text, template_id = build_refuse_not_understood()
            _log_fallback(template_id, detail="finalize_empty_content")
            return {"result": {"content": text, "image": [], "references": []}}
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
        """解析 supervisor 输出任务列表；非法 JSON 时退回空任务（走 finalize 兜底）。

        B-17：每次解析按输出契约记录格式事件（supervisor_tasks），行为不变——
        空任务仍由路由走 finalize 兜底，坏任务不透传下游。"""
        try:
            obj = json.loads(content)
        except (TypeError, ValueError):
            _contract_stats().record("supervisor_tasks", False, ["supervisor 输出非 JSON（B-17）"])
            return [], content
        tasks: List[Dict[str, str]] = []
        if isinstance(obj, dict):
            raw_tasks = obj.get("tasks") or []
            for t in raw_tasks:
                if isinstance(t, dict) and t.get("agent") in ("financial", "research") and (t.get("query") or "").strip():
                    tasks.append({"agent": str(t["agent"]), "query": str(t["query"]).strip()})
            res = _contract_supervisor(obj)
            _contract_stats().record("supervisor_tasks", res.ok, res.errors)
            return tasks, obj.get("direct_answer")
        if isinstance(obj, list):
            for t in obj:
                if isinstance(t, dict) and t.get("agent") in ("financial", "research") and (t.get("query") or "").strip():
                    tasks.append({"agent": str(t["agent"]), "query": str(t["query"]).strip()})
            res = _contract_supervisor(obj)
            _contract_stats().record("supervisor_tasks", res.ok, res.errors)
            return tasks, None
        _contract_stats().record("supervisor_tasks", False, ["supervisor 输出结构非法（B-17）"])
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
        research_items = subtask_results.get("research") or []
        # 引用口径（B-09 修复）：研报子 Agent 返回的引用已过 L1 文件可溯源过滤，
        # 优先以其为准；aggregator LLM 自拟的 references 可能含 example.com 等占位假引用，
        # 故仅在“没有研报子任务”时才保留其结果引用（且剔除明显 URL 占位）。
        refs: List[Dict[str, Any]] = []
        if research_items:
            existing_paths = set()
            for item in research_items:
                raw = item.get("raw") or {}
                for r in raw.get("references") or []:
                    if not isinstance(r, dict) or not r.get("paper_path"):
                        continue
                    path = str(r.get("paper_path"))
                    if path in existing_paths:
                        continue
                    refs.append(r)
                    existing_paths.add(path)
        else:
            for r in result.get("references") or []:
                if not isinstance(r, dict) or not r.get("paper_path"):
                    continue
                path = str(r.get("paper_path"))
                lowered = path.lower()
                if lowered.startswith("http://") or lowered.startswith("https://"):
                    continue
                refs.append(r)
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
