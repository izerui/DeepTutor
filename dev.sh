#!/usr/bin/env bash
#
# 开发快捷入口：上下方向键选择，回车执行。
#
# docs/guides/local-development.md 保持使用原始命令，不依赖本脚本。
set -euo pipefail

# 允许在任何目录下调用
cd "$(dirname "${BASH_SOURCE[0]}")"

VENV_DIR=".venv"

print_command() {
  printf '\n$'
  printf ' %q' "$@"
  printf '\n\n'
}

# 所有后端命令都必须跑在本项目的虚拟环境里，否则会误用全局 Python 或别的项目的依赖。
# 只认 DeepTutor 自己的 .venv：外面激活的是别家 venv 时也要切回来。
ensure_venv() {
  if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    echo "找不到虚拟环境 ${VENV_DIR}，请先执行菜单里的「初始化开发环境」。" >&2
    return 1
  fi

  local project_venv
  project_venv=$(cd "$VENV_DIR" && pwd -P)

  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    local active_venv=""
    if [[ -d "$VIRTUAL_ENV" ]]; then
      active_venv=$(cd "$VIRTUAL_ENV" && pwd -P)
    fi
    if [[ "$active_venv" == "$project_venv" ]]; then
      return 0
    fi
    echo "⚠ 当前激活的虚拟环境是 ${VIRTUAL_ENV}，已切换到 ${project_venv}。" >&2
  fi

  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
}

require_web_deps() {
  if [[ ! -d "web/node_modules" ]]; then
    echo "找不到 web/node_modules，请先执行菜单里的「初始化开发环境」。" >&2
    return 1
  fi
}

setup_environment() {
  if [[ ! -f "$VENV_DIR/bin/activate" ]]; then
    print_command python3 -m venv "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
  else
    echo "✔ 已存在虚拟环境 ${VENV_DIR}，跳过创建。"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"

  print_command pip install -e ".[all]"
  pip install -e ".[all]"

  print_command npm ci --legacy-peer-deps
  (cd web && npm ci --legacy-peer-deps)

  if command -v pre-commit >/dev/null 2>&1; then
    print_command pre-commit install
    pre-commit install
  else
    echo "⚠ 找不到 pre-commit，已跳过 hooks 安装。" >&2
  fi

  echo
  echo "✔ 开发环境就绪。配置文件在 data/user/settings/，首次可在 Web UI 的 Settings 页面填 API key。"
}

cursor_hidden=0

restore_cursor() {
  if [[ "$cursor_hidden" -eq 1 ]]; then
    printf '\033[?25h'
    cursor_hidden=0
  fi
}

trap restore_cursor EXIT
trap 'exit 130' INT TERM

labels=(
  "一键启动（开发模式，前端热重载）"
  "一键启动（生产模式，先 build 再 serve）"
  "只启动后端"
  "只启动前端"
  "只启动前端（Turbopack）"
  "跑后端测试"
  "前端快速检查（类型 + 测试 + lint）"
  "前端全量检查（含构建 + 性能预算）"
  "代码检查（pre-commit 全量）"
  "初始化开发环境"
  "退出"
)
commands=(
  "deeptutor start --home . --dev"
  "deeptutor start --home ."
  "deeptutor serve --reload"
  "cd web && npm run dev"
  "cd web && npm run dev:turbo"
  "pytest tests/"
  "cd web && npm run check:fast"
  "cd web && npm run check"
  "pre-commit run --all-files"
  ""
  ""
)
actions=(
  "start-dev"
  "start-prod"
  "backend"
  "frontend"
  "frontend-turbo"
  "pytest"
  "web-check-fast"
  "web-check"
  "precommit"
  "setup"
  "exit"
)

choose_interactive_action() {
  local selected=0
  local rendered=0
  local key
  local sequence
  local i
  local lines=$((${#labels[@]} + 4))

  printf '\033[?25l'
  cursor_hidden=1

  while true; do
    if [[ "$rendered" -eq 1 ]]; then
      printf '\033[%dA' "$lines"
    fi

    printf '\033[2K\r请选择要执行的操作：\n'
    printf '\033[2K\r\n'
    for i in "${!labels[@]}"; do
      if [[ "$i" -eq "$selected" ]]; then
        printf '\033[2K\r\033[1;36m> %s\033[0m' "${labels[$i]}"
        if [[ -n "${commands[$i]}" ]]; then
          printf '\033[1;36m    %s\033[0m' "${commands[$i]}"
        fi
        printf '\n'
      else
        printf '\033[2K\r  %s' "${labels[$i]}"
        if [[ -n "${commands[$i]}" ]]; then
          printf '    %s' "${commands[$i]}"
        fi
        printf '\n'
      fi
    done
    printf '\033[2K\r\n'
    printf '\033[2K\r↑/↓ 选择，Enter 执行，q 退出\n'
    rendered=1

    if ! IFS= read -rsn1 key; then
      action="exit"
      break
    fi

    case "$key" in
      $'\x1b')
        sequence=""
        IFS= read -rsn2 sequence || true
        case "$sequence" in
          '[A')
            selected=$(((selected + ${#labels[@]} - 1) % ${#labels[@]}))
            ;;
          '[B')
            selected=$(((selected + 1) % ${#labels[@]}))
            ;;
        esac
        ;;
      '')
        action="${actions[$selected]}"
        break
        ;;
      q|Q)
        action="exit"
        break
        ;;
    esac
  done

  restore_cursor
  printf '\n'
}

if [[ "$#" -ne 0 ]]; then
  echo "dev.sh 不接受参数，请直接运行 ./dev.sh" >&2
  exit 1
fi

if [[ ! -t 0 || ! -t 1 ]]; then
  echo "dev.sh 需要交互式终端，请直接运行 ./dev.sh" >&2
  exit 1
fi

action=""
choose_interactive_action

case "$action" in
  start-dev)
    ensure_venv
    echo "▶ 后端 http://localhost:8001，前端 http://localhost:3782。Ctrl+C 停止。"
    print_command deeptutor start --home . --dev
    exec deeptutor start --home . --dev
    ;;

  start-prod)
    ensure_venv
    echo "▶ 生产模式会先 build 前端，首次启动较慢。Ctrl+C 停止。"
    print_command deeptutor start --home .
    exec deeptutor start --home .
    ;;

  backend)
    ensure_venv
    echo "▶ 后端 http://localhost:8001，已开启热重载。Ctrl+C 停止。"
    print_command deeptutor serve --reload
    exec deeptutor serve --reload
    ;;

  frontend)
    require_web_deps
    echo "▶ 前端 http://localhost:3782，需要后端已在另一个终端跑起来。Ctrl+C 停止。"
    print_command npm run dev
    cd web && exec npm run dev
    ;;

  frontend-turbo)
    require_web_deps
    echo "▶ 前端 http://localhost:3782（Turbopack）。Ctrl+C 停止。"
    print_command npm run dev:turbo
    cd web && exec npm run dev:turbo
    ;;

  pytest)
    ensure_venv
    print_command pytest tests/
    exec pytest tests/
    ;;

  web-check-fast)
    require_web_deps
    print_command npm run check:fast
    cd web && exec npm run check:fast
    ;;

  web-check)
    require_web_deps
    print_command npm run check
    cd web && exec npm run check
    ;;

  precommit)
    ensure_venv
    print_command pre-commit run --all-files
    exec pre-commit run --all-files
    ;;

  setup)
    setup_environment
    ;;

  exit)
    exit 0
    ;;
esac
