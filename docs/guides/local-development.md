# DeepTutor 本地开发指南

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ / FastAPI / Uvicorn |
| 前端 | Next.js (React) / TypeScript |
| 数据存储 | SQLite（聊天记录）+ JSON 文件（用户、配置） |
| 向量检索 | LlamaIndex + FAISS |
| 包管理 | pip (Python) / npm (前端) |

> PocketBase 默认不启用。Docker Compose 提供可选的 PocketBase sidecar；原生本地运行
> 也可通过配置 `integrations.json` 中的 `pocketbase_url` 连接外部 PocketBase 实例。

## 环境搭建

```bash
# 1) 创建 Python 虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2) 安装 Python 依赖（含开发工具）
pip install -e ".[all]"

# 3) 安装前端依赖
cd web && npm ci --legacy-peer-deps && cd ..

# 4) 安装 pre-commit hooks（首次）
pre-commit install
```

## 核心配置文件

所有运行时配置都在 `data/user/settings/` 目录下，不需要 `.env` 文件（`.env.example` 只用于 Docker Compose 场景）：

| 文件 | 作用 |
|---|---|
| `system.json` | 端口配置（backend: 8001, frontend: 3782）、CORS、附件限制等 |
| `model_catalog.json` | LLM API 密钥和模型配置 |
| `main.yaml` | 各能力模块的 temperature、max_tokens 等参数 |
| `agents.yaml` | Agent 能力的详细参数（solve/research/question 等） |
| `auth.json` | 认证开关、管理员用户名、token 过期时间 |

### 用户与认证存储

原生本地运行时，用户账号存储在 `data/system/auth/users.json`（bcrypt 密码哈希）。
JWT 签名密钥 `data/system/auth/auth_secret` 在首次启动时自动生成，仅用于本地
会话签名。**不要从生产环境复制 `auth_secret`**——它会扩大泄漏和伪造线上令牌的风险。

### 配置 LLM API 密钥

`model_catalog.json` 是模型配置的核心，需要在 `connections` 和 `services.llm.profiles` 中配置 API key。最简单的方式是启动应用后在 Web UI 的 Settings 页面中配置，它会自动写入这个文件。

## 启动项目

### 方式一：一键启动（推荐）

```bash
# 生产模式（先 build 前端再 serve）
deeptutor start --home .

# 开发模式（前端热重载）
deeptutor start --home . --dev

# 不自动打开浏览器
deeptutor start --home . --dev --no-browser
```

这条命令会同时启动：
- 后端 API 服务 → `http://localhost:8001`
- 前端 Web 服务 → `http://localhost:3782`

### 方式二：分别启动（方便调试）

**终端 1 — 后端：**

```bash
source .venv/bin/activate
deeptutor serve --reload
```

后端监听 `http://localhost:8001`，`--reload` 开启代码热重载。

**终端 2 — 前端：**

```bash
cd web
npm run dev          # 常规开发模式
# 或
npm run dev:turbo    # Turbopack 加速模式
```

前端监听 `http://localhost:3782`。

## 端口说明

| 服务 | 默认端口 | 配置位置 |
|---|---|---|
| 后端 API | 8001 | `data/user/settings/system.json` → `backend_port` |
| 前端 Web | 3782 | `data/user/settings/system.json` → `frontend_port` |
| PocketBase | 8090 | `data/user/settings/integrations.json`（默认不启用） |

## 常用开发命令

```bash
# 跑测试
pytest tests/

# 代码检查
pre-commit run --all-files

# 前端类型检查
cd web && npm run typecheck

# 前端单元测试
cd web && npm run test:unit

# 前端快速检查（类型 + 测试 + lint）
cd web && npm run check:fast

# 前端全量检查（含构建 + 性能预算）
cd web && npm run check
```

## Docker 开发（可选）

```bash
cp .env.example .env    # 按需修改端口
# 开发模式（挂载源码，支持热重载）
python scripts/docker_compose.py -f docker-compose.yml -f docker-compose.dev.yml up
```

> 使用项目提供的 `scripts/docker_compose.py` 而非直接调用 `docker compose`，
> 以确保端口映射和运行时适配逻辑正确应用。

## 项目目录结构

```
deeptutor/              # Python 后端核心
  api/                  #   FastAPI 路由和 API 端点
  services/             #   业务服务（config、RAG、LLM 等）
  learning/             #   学习功能模块
  runtime/              #   运行时（启动器、模式管理）
deeptutor_cli/          # CLI 入口（deeptutor 命令）
web/                    # Next.js 前端
  app/                  #   App Router 页面
  features/             #   功能模块
  components/           #   共享组件
  lib/                  #   工具库
data/
  user/settings/        #   运行时配置（gitignored）
  system/auth/          #   用户账号和 JWT 密钥（gitignored）
tests/                  # 测试
scripts/                # 工具脚本
```
