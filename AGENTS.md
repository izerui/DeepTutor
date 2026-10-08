# DeepTutor — Agent-Native Architecture

## Overview

DeepTutor is an **agent-native** intelligent learning companion organized
around a two-layer plugin model — single-shot **Tools** invoked by the
LLM, and multi-stage **Capabilities** that take over a turn — exposed
through three entry points: CLI, WebSocket API, and Python SDK. All three
enter the durable turn application service before the shared turn engine
routes a normalized context to the selected capability.

## Branch Maintenance Policy

`main` is the upstream baseline. `develop` builds on it with only necessary
local adaptations and extensions; it must not maintain a parallel implementation
of functionality already covered by `main`.

- When `main` provides the required capability, adopt its implementation directly
  and remove the corresponding local duplicate. Prefer upstream code unchanged
  over a locally rewritten equivalent.
- Retain local extensions only for requirements or safeguards that `main` does
  not yet cover. Keep these changes narrowly scoped, isolate them in dedicated
  modules where practical, and minimize edits to upstream-owned code.
- On every merge from `main`, reassess existing local extensions. If upstream
  now covers them, retire them instead of preserving both implementations.
- Verify behavioral and security coverage, not just matching feature names,
  before replacing a local implementation. Preserve required safeguards that
  upstream has not yet implemented.
- Preserve compatibility with existing local data and settings when retiring
  extensions. Add explicit compatibility handling where needed; do not silently
  discard data or broaden permissions.
- Validate both newly adopted upstream behavior and retained local extensions.
  Document any remaining differences; a conflict-free Git merge alone does not
  prove functional compatibility.

### CI / Docker Workflow Maintenance

本项目是从上游 clone 过来的独立部署实例。上游的正式发布流程（`docker-release.yml`、
`pypi-release.yml`、`tests.yml`、`repository-hygiene.yml`）由源工程负责，本项目
不需要保留这些文件。

- **`develop-image.yml`** 是本项目自有的镜像构建工作流，独立于上游的
  `docker-release.yml`，两者触发条件、平台、build-args 完全不同，不要改名合并。
- 每次从 `main` 合并后，检查 **Dockerfile** 是否新增了 `ARG` 或构建阶段变化：
  ```bash
  git diff HEAD~1..HEAD -- Dockerfile
  ```
  如果 Dockerfile 新增了 build-arg，评估是否需要同步到 `develop-image.yml` 的
  `build-args` 中。
- `develop-image.yml` 已经传递了完整的 build-args（`DEEPTUTOR_REQUIREMENTS_FILE`、
  `DEEPTUTOR_BUILD_APT_PACKAGES`、`DEEPTUTOR_RUNTIME_APT_PACKAGES`、
  `DEEPTUTOR_RUNTIME_FONT_PACKAGES`、`DEEPTUTOR_VERIFY_DEPLOYMENT_EXTRAS`），
  覆盖范围比上游的正式发布镜像更全（含 CJK 字体、LaTeX 渲染、完整 deployment 依赖）。
- 合并 `main` 时如果 git 试图恢复上游已删除的 CI 工作流文件，应继续保持删除，
  不要恢复。

## Architecture

```
Entry Points:  CLI (Typer)  |  WebSocket /ws  |  Python SDK
                    ↓                   ↓                   ↓
              ┌─────────────────────────────────────────────────┐
              │          TurnApplicationService                 │
              │   persists, coordinates, and replays turns      │
              └──────────────────────┬──────────────────────────┘
                                     ↓
              ┌─────────────────────────────────────────────────┐
              │       TurnEngine → ChatOrchestrator              │
              │   routes UnifiedContext → selected Capability    │
              │   (defaults to `chat`)                           │
              └──────────┬──────────────┬───────────────────────┘
                         │              │
              ┌──────────▼──┐  ┌────────▼──────────┐
              │ ToolRegistry │  │ CapabilityRegistry │
              │  (Level 1)   │  │   (Level 2)        │
              └──────────────┘  └────────────────────┘
```

`TurnApplicationService` owns durable turn state and replay through the
session store and runtime coordinator. Each execution uses a per-turn
`StreamBus`; the orchestrator emits events, the turn runtime persists them,
and adapters replay them to consumers. Runtime settings live in
`data/user/settings/*.json` — project-root `.env` files are intentionally
ignored.

### Level 1 — Tools

Single-function tools the LLM picks on demand. Seven user-toggleable tools
surface in `/settings/tools`:

| Tool                 | Description                                   |
| -------------------- | --------------------------------------------- |
| `brainstorm`         | Breadth-first idea exploration with rationale |
| `web_search`         | Web search with citations                     |
| `paper_search`       | arXiv preprint search                         |
| `reason`             | Dedicated deep-reasoning LLM call             |
| `geogebra_analysis`  | Analyze math images into GeoGebra commands    |
| `imagegen`           | Generate images                               |
| `videogen`           | Generate videos                               |

`USER_TOGGLEABLE_TOOL_NAMES` in `deeptutor/tools/builtin/__init__.py` is the
authoritative toggle list. Other built-ins are **context-gated** or
capability-owned: `CONFIGURABLE_BUILTIN_TOOL_NAMES` declares the context-gated
surface, while `deeptutor/agents/_shared/tool_composition.py` owns the mount
rules (`ToolMountFlags`) and the always-available workspace tools. Examples
include `rag`, memory and notebook tools, `read_skill`, deferred MCP/CLI tools,
`exec`, `ask_user`, and mastery navigation. `--tool` selects from the
user-toggleable whitelist; it does not bypass context or capability gates.

### Level 2 — Capabilities

Multi-stage pipelines that own the turn:

| Capability       | Stages                                                |
| ---------------- | ----------------------------------------------------- |
| `chat`           | exploring → responding (single agentic loop, default) |
| `ask_questions`  | responding (chat loop forced through `ask_user`)      |
| `deep_solve`     | responding (chat loop + solve planning tools)         |
| `deep_question`  | ideation → generation                                 |
| `deep_research`  | rephrasing → decomposing → researching → reporting    |
| `visualize`      | analyzing → generating → reviewing (SVG / Chart.js / Mermaid / HTML; or routes to Manim sub-stages via `render_type`) |
| `math_animator`  | concept_analysis → concept_design → code_generation → code_retry → summary → render_output |
| `mastery_path`   | responding (Guided Learning — chat loop + mastery tools, gated per topic type) |
| `immersive_reading` | responding (document-grounded reading loop)        |
| `course_study`   | responding (course-state sensing and hand-off loop)   |
| `immersive_watching` | responding (timestamp-grounded video loop)         |

All capabilities converge on `emit_capability_result()` in
`deeptutor/capabilities/_shared.py` so every turn emits the same envelope
(response payload + `cost_summary` from `UsageTracker`). Status copy and
prompts are i18n'd via `capabilities/prompts/{en,zh}/<name>.yaml`.

## CLI Usage

```bash
# Install
pip install deeptutor      # Full app (CLI + Web/API + packaged Web assets)
pip install deeptutor-cli  # CLI-only

# Run any capability
deeptutor run chat "Explain Fourier transform"
deeptutor run deep_solve "Solve x^2=4" -t rag --kb my-kb
deeptutor run visualize "Animate sine wave" --config render_mode=manim_video

# Interactive REPL
deeptutor chat
# (inside the REPL: /regenerate or /retry re-runs the last user message)

# Partners (IM-connected companions)
deeptutor partner list

# Knowledge bases, memory, server
deeptutor kb list
deeptutor kb create my-kb --doc textbook.pdf
deeptutor memory show
deeptutor serve --port 8001       # API server only
deeptutor start                   # backend + frontend together
```

## Key Files

| Path                                       | Purpose                              |
| ------------------------------------------ | ------------------------------------ |
| `deeptutor/runtime/orchestrator.py`        | `ChatOrchestrator` — unified entry   |
| `deeptutor/runtime/launcher.py`            | Backend + frontend lifecycle / port discovery |
| `deeptutor/runtime/registry/`              | Tool + Capability registries         |
| `deeptutor/runtime/bootstrap/builtin_capabilities.py` | Built-in capability class paths |
| `deeptutor/services/config/runtime_settings.py` | JSON settings + process-env overrides |
| `deeptutor/services/subagent/`             | Local/remote agent connectors; register each backend in `registry.py` and its model options in `models.py` (Grok CLI uses native `streaming-json`) |
| `deeptutor/core/stream.py`, `deeptutor/runtime/stream_bus.py` | StreamEvent protocol + async fan-out |
| `deeptutor/core/tool_protocol.py`          | `BaseTool` + `ToolDefinition`         |
| `deeptutor/core/capability_protocol.py`    | `TurnCapability` + `CapabilityManifest` |
| `deeptutor/core/context.py`                | `UnifiedContext` dataclass            |
| `deeptutor/tools/builtin/__init__.py`      | All built-in tool wrappers           |
| `deeptutor/capabilities/`                  | Built-in capability implementations  |
| `deeptutor/app.py`                         | `DeepTutorApp` — Python SDK facade    |
| `deeptutor_cli/main.py`                    | Typer CLI entry point                |
| `deeptutor/api/routers/unified_ws.py`      | Unified WebSocket endpoint           |

## Dependency Layers

Public install paths and source extras are defined in `pyproject.toml`.
Requirements files mirror the same dependency groups for Docker/CI installs.

```
pip install deeptutor      — Full app (CLI + Web/API + packaged Web assets)
pip install deeptutor-cli  — CLI-only (LLM + RAG + providers + document parsing)
pip install -e .           — Source install for development

Source extras (.[ extra ], defined in pyproject.toml):
.[cli]            — CLI-only dependency set
.[server]         — Web/API server dependencies
.[partners]       — Partner channel SDKs  (legacy alias: .[tutorbot])
.[matrix]         — Matrix channel for Partners (matrix-nio; needs libolm)
.[matrix-e2e]     — Matrix with end-to-end encryption (matrix-nio[e2e])
.[math-animator]  — Manim addon (powers `visualize` Manim renders + `deeptutor run math_animator`)
.[dev]            — Test / lint tooling
.[all]            — Everything above
```

## Production k3s Deployment (生产环境)

Use these fixed entry points when the user asks to inspect the deployed service:

- kubeconfig: `/Users/liuyuhua/.kube/config_k3s`
- namespace: `prod`
- Deployment: `deeptutor`
- Pod selector: `app=deeptutor`
- application container: `deeptutor`
- deployed image: `serv999.com/ghcr/izerui/deeptutor:develop`
- Kubernetes manifest: `/Users/liuyuhua/IdeaProjects/kubernetes/cluster/k3s/init/prod/deeptutor/app.yaml`

```bash
export KUBECONFIG=/Users/liuyuhua/.kube/config_k3s
kubectl -n prod get pods -l app=deeptutor
kubectl -n prod logs deploy/deeptutor --since=30m
```

### Debugging & Troubleshooting (线上调试)

Always set `KUBECONFIG` first:
```bash
export KUBECONFIG=/Users/liuyuhua/.kube/config_k3s
POD=$(kubectl -n prod get pods -l app=deeptutor -o jsonpath='{.items[0].metadata.name}')
```

Common operations:
```bash
# View recent backend logs (filter by keyword)
kubectl -n prod logs $POD -c deeptutor --since=10m | grep -i "error\|fail\|warning"

# Tail logs in real time
kubectl -n prod logs $POD -c deeptutor -f

# Check process status inside the Pod
kubectl -n prod exec $POD -c deeptutor -- ps aux

# Exec into the Pod for interactive debugging
kubectl -n prod exec -it $POD -c deeptutor -- bash

# Check a specific Python file on the Pod
kubectl -n prod exec $POD -c deeptutor -- cat /app/deeptutor/path/to/file.py

# Check frontend build on the Pod
kubectl -n prod exec $POD -c deeptutor -- cat /app/web/.next/BUILD_ID

# Restart backend only (supervisor auto-restarts)
kubectl -n prod exec $POD -c deeptutor -- bash -c 'kill $(pgrep -f uvicorn)'

# Restart frontend only
kubectl -n prod exec $POD -c deeptutor -- bash -c 'kill -9 $(pgrep -f next-server)'

# View Pod events and resource usage
kubectl -n prod describe pod $POD
kubectl -n prod top pod $POD
```

When investigating bugs reported by the user, check the Pod logs first to find backend errors, then correlate with the frontend behavior.

### Hot Deploy (热部署)

When the user says "热部署" or "hot deploy", run the hot-deploy script to push local changes to the running Pod without rebuilding the image.

```bash
# Deploy both frontend and backend
./scripts/hot-deploy.sh

# Backend only (seconds, just cp Python files + restart uvicorn)
./scripts/hot-deploy.sh backend

# Frontend only (requires local npm run build, ~1-2 min)
./scripts/hot-deploy.sh frontend
```

How it works:
- **Backend**: archive and atomically replace the complete local `deeptutor/` package, including untracked additions and local deletions, then restart uvicorn. The previous package is retained until `/health/ready` is stable and is restored automatically on failure.
- **Frontend**: intended only for application-code debugging. It compares `package.json`, lockfiles, and `next.config.*` with a content manifest embedded in the running image and refuses deployment when they differ; those changes require a Linux-targeted image build. Otherwise it builds locally, preserves the Pod's image-provided Linux `node_modules`, and replaces only `server.js`, `.next`, and `public`.
- **Combined deployment**: prepares and uploads both artifacts before changing either service. Backend and frontend backups are retained until both versions pass stable health checks; a frontend failure triggers coordinated frontend and backend rollback.
- Process manager: supervisord (PID 1), programs: `backend` (uvicorn) and `frontend` (node server.js).
