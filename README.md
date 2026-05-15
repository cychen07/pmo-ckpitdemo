# PMO 智能座舱 · pmo-ckpitdemo

> Editorial Mission Control for workflow / job / observability — built with FastAPI, React, Lark.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white)](https://vitejs.dev/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)

**PMO 智能座舱** 是一个面向项目交付总监的"任务流 / 执行链 / 可观测 / 告警运营"一体化控制台，采用 *Editorial Mission Control* 视觉风格——暖纸面、墨黑 hairline、朱砂红强调色，把 NASA 控台的克制和编辑刊物的呼吸感带进 PMO 工作流。

⸻

## ✨ 特性

- **任务流编排**：模板化 Workflow + 状态机驱动的 Workitem 流转，一键 instantiate 与升级
- **执行链可视化**：Mission Canvas，节点高亮 + 朱砂红呼吸动画 + 决策菱形分支
- **执行舱（Job Drawer）**：异步任务追踪、超时/重试/死信全程可观测
- **告警运营**：按 cluster_key 去重、Silence/Resend 一键映射为飞书 Ops Task，双向追踪
- **飞书集成**：Webhook + OpenAPI 双通道，凭证未配置时自动降级为本地 mock
- **可观测性**：`/readyz` 凭证预检、KPI 卡片、live-pulse 状态点、SSE 事件流
- **无障碍**：`prefers-reduced-motion` 下自动禁用呼吸动画

## 🚀 5 分钟启动（Docker Compose）

唯一前置要求：**Docker 25+** 且开启 Compose v2。

```bash
git clone https://github.com/cychen07/pmo-ckpitdemo.git
cd pmo-ckpitdemo
cp .env.example .env       # 默认值即可跑起来；如需飞书集成可填 LARK_APP_ID / LARK_APP_SECRET
docker compose up -d
```

打开浏览器：

| 服务 | URL |
|---|---|
| **前端控制台** | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| API 文档（Swagger） | http://localhost:8000/docs |
| 健康检查 | http://localhost:8000/readyz |

停止与清理：

```bash
docker compose down          # 停服务，保留数据
docker compose down -v       # 停服务，清空所有 volume（重置）
```

## 📦 系统架构

```
┌──────────────────┐    HTTP/SSE     ┌───────────────────────┐
│  Browser (3000)  │ ──────────────▶ │   FastAPI (8000)      │
│  React + Vite    │                  │   Control Plane       │
│  Editorial UI    │ ◀────────────── │                       │
└──────────────────┘                  └───────────┬───────────┘
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          ▼                       ▼                       ▼
                   ┌─────────────┐        ┌─────────────┐         ┌──────────────┐
                   │ PostgreSQL  │        │   Redis     │         │  Lark / 飞书 │
                   │ (snapshot)  │        │  (queue)    │         │ (可选)       │
                   └─────────────┘        └─────────────┘         └──────────────┘
```

| 容器 | 镜像 | 端口 | 用途 |
|---|---|---|---|
| pmo-frontend | `nginx:1.27-alpine` | 3000 → 80 | Vite 静态产物 + 运行时 API_BASE 注入 |
| pmo-backend | `python:3.13-slim` | 8000 | FastAPI 控制面（workflow / job / metrics / alerting / SSE） |
| pmo-postgres | `postgres:16-alpine` | 内网 | 状态快照持久化 |
| pmo-redis | `redis:7-alpine` | 内网 | 任务队列与去重 |

## ⚙️ 配置一览

主要环境变量（完整列表见 [.env.example](.env.example)）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `FRONTEND_PORT` | `3000` | 前端宿主机端口 |
| `BACKEND_PORT` | `8000` | 后端宿主机端口 |
| `PUBLIC_API_BASE` | `http://localhost:8000` | 浏览器侧访问后端的地址（远程部署时改为公网域名/IP） |
| `NEWERA_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS 白名单，逗号分隔；`*` 表示允许全部（关闭 credentials） |
| `POSTGRES_PASSWORD` | `pmo_dev_password` | **生产部署务必修改** |
| `LARK_APP_ID` / `LARK_APP_SECRET` | 空 | 飞书自建应用凭证 |
| `LARK_BOT_WEBHOOK_URL` | 空 | 飞书机器人 Webhook |
| `NEWERA_DELIVERY_PROVIDER` | `local` | `local` / `lark` |
| `NEWERA_OPS_TASK_PROVIDER` | `local` | `local` / `lark_webhook` / `lark_task_api` |
| `NEWERA_STARTUP_STRICT` | `false` | true 时凭证缺失会拒绝启动 |

## 🛠 本地开发（不走 Docker）

### 后端

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev   # http://localhost:5173
```

### 测试

```bash
# 后端
cd backend && python -m pytest

# 前端
cd frontend && npm run test
```

## 🎨 设计系统：Editorial Mission Control

| 维度 | 选择 |
|---|---|
| 底色 | Warm paper `#f5efe6` |
| 文本 | Ink black `#1a1612` |
| 主色 | Vermillion `#b8392f` |
| 语义辅色 | Navy `#284b78` · Moss `#4f7a3f` · Amber `#c79a1a` · Burnt orange `#c0631c` · Plum `#6e3a73` |
| Display 字体 | [Fraunces](https://fonts.google.com/specimen/Fraunces) (variable opsz) |
| Body 字体 | IBM Plex Sans / Plex Sans SC |
| Mono 字体 | JetBrains Mono（启用 tabular-nums） |
| 形态 | 硬角矩形 + hairline rule + offset shadow |
| 动效 | `prefers-reduced-motion` 下全部禁用呼吸动画 |

## 🗺 路线图

- [x] OBJ-01 / OBJ-02：领域模型 + 状态机
- [x] API-01 / API-02：Workflow / Workitem 动作接口、Executor 推荐
- [x] UI-01 ~ UI-05：调度台、执行链、执行舱、决策抽屉
- [x] Editorial Mission Control 视觉重构
- [x] Docker 一键部署
- [ ] GitHub Actions CI（pytest + vitest 自动跑）
- [ ] 预构建镜像推送 GHCR
- [ ] Helm Chart（Kubernetes 部署）
- [ ] 多租户与 RBAC 增强

## 🤝 贡献

欢迎 Issue / PR。提 PR 前请确保：

```bash
cd backend && python -m pytest
cd frontend && npm run test && npm run build
```

## 📄 License

[MIT](LICENSE) © 2026 cychen07
