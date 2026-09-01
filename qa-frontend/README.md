# 智能问数前端：React 19 + Vite

上市公司研报"智能问数"系统的 Web 界面，与 FastAPI 后端（`uvicorn app.api:app`）联调。

## 功能

- 统一 Agent 入口：所有问题统一走 Agent 分析（`POST /chat/stream`，`mode=agent`），由 LangGraph supervisor 自动路由到财务 SQL 或研报 RAG 子 Agent
- 流式输出：SSE 逐 token 渲染内容，结束返回引用与图表
- 多会话管理：最多 5 个对话（localStorage 持久化），会话按 `user_id` 与后端记忆对齐
- 引用核验详情：每条引用可展开（`paper_path` / 摘要 / 数字命中明细）
- 结果图表：Agent 财务查询生成的 ECharts 图表展示（`/result` 静态资源）
- 自研 RAG 问答（`/chat` mode=rag）与多轮澄清（`/chat/clarify`）保留在后端，供 CLI 与本地回归使用

## 本地开发

```bash
npm install
npm run dev        # http://localhost:5173（VITE_API_URL 指向后端，默认 http://localhost:8000）
```

## 生产构建

```bash
npm run build      # 产物 dist/，由 Docker Compose 的 Nginx 镜像托管（见 docker-compose.yml）
```

> 环境变量：`VITE_API_URL` 可覆盖后端地址；本地联调时确认与后端端口一致。
