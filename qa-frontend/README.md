# 智能问数前端（React 19 + Vite）

上市公司研报"智能问数"系统的 Web 界面，与 FastAPI 后端（`uvicorn app.api:app`）联调。

## 功能

- 问答对话：普通 RAG 问答（`POST /chat`）与 Agent 模式（`mode=agent`）切换
- 流式输出：`POST /chat/stream`（SSE），逐 token 渲染内容，结束返回引用
- 引用详情：每条引用可展开（`paper_path` / 摘要 / 数字命中明细）
- 结果图表：Agent 财务查询生成的 ECharts 图片展示（`/result` 静态资源）
- 多轮澄清：字段缺失时展示澄清问题与追问交互

## 本地开发

```bash
npm install
npm run dev        # http://localhost:5173（默认代理 /api 到 http://localhost:8000）
```

## 生产构建

```bash
npm run build      # 产物 dist/，由 Docker Compose 的 Nginx 镜像托管（见 docker-compose.yml）
```

> 环境变量：`VITE_API_URL` 可覆盖后端地址；本地联调时确认与后端端口一致。
