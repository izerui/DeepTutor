#!/usr/bin/env bash
set -euo pipefail

# DeepTutor 热部署脚本
# 用法:
#   ./scripts/hot-deploy.sh              # 部署前端+后端
#   ./scripts/hot-deploy.sh backend      # 仅部署后端
#   ./scripts/hot-deploy.sh frontend     # 仅部署前端

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/deeptutor-hot-deploy.XXXXXX")"

export KUBECONFIG="${KUBECONFIG:-/Users/liuyuhua/.kube/config_k3s}"
NAMESPACE="prod"
LABEL="app=deeptutor"
CONTAINER="deeptutor"
BACKEND_PROCESS_PATTERN='^python -m uvicorn deeptutor\.api\.main:app'
FRONTEND_PROCESS_PATTERN='^next-server'
BACKEND_HEALTH_URL='http://127.0.0.1:8001/health/ready'
FRONTEND_HEALTH_URL='http://127.0.0.1:3782/login'
HEALTH_ATTEMPTS=45
HEALTH_STABLE_CHECKS=5

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

get_pod() {
  kubectl -n "$NAMESPACE" get pods -l "$LABEL" \
    --field-selector=status.phase=Running \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

wait_for_service() {
  local pod="$1"
  local label="$2"
  local process_pattern="$3"
  local health_url="$4"
  local previous_pid="${5:-}"
  local last_pid=""
  local stable_checks=0
  local attempt

  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    local result=""
    result=$(kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- \
      bash -c '
        pid="$(pgrep -u deeptutor -o -f "$1" 2>/dev/null || true)"
        code="$(curl -sS --max-time 2 -o /dev/null -w "%{http_code}" "$2" 2>/dev/null || true)"
        printf "%s %s" "$pid" "$code"
      ' _ "$process_pattern" "$health_url" 2>/dev/null || true)

    local pid="${result%% *}"
    local code="${result##* }"
    if [[ -n "$pid" && "$pid" != "$previous_pid" && "$code" == "200" ]]; then
      if [[ "$pid" == "$last_pid" ]]; then
        stable_checks=$((stable_checks + 1))
      else
        last_pid="$pid"
        stable_checks=1
      fi
      if ((stable_checks >= HEALTH_STABLE_CHECKS)); then
        echo "    ✓ $label 已就绪并稳定运行 (PID: $pid)"
        return 0
      fi
    else
      last_pid=""
      stable_checks=0
    fi
    sleep 1
  done

  echo "    ✗ $label 未在 ${HEALTH_ATTEMPTS}s 内稳定就绪"
  return 1
}

restart_process() {
  local pod="$1"
  local process_pattern="$2"
  kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- \
    bash -c '
      pid="$(pgrep -u deeptutor -o -f "$1" 2>/dev/null || true)"
      if [[ -n "$pid" ]]; then
        kill "$pid" 2>/dev/null || true
      fi
      printf "%s" "$pid"
    ' _ "$process_pattern"
}

rollback_backend() {
  local pod="$1"
  echo "    正在回滚后端..."
  kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    rm -rf /app/deeptutor
    mv /app/deeptutor.hot-deploy.bak /app/deeptutor
    chown -R deeptutor:deeptutor /app/deeptutor
  '
  local failed_pid
  failed_pid=$(restart_process "$pod" "$BACKEND_PROCESS_PATTERN")
  wait_for_service \
    "$pod" "后端回滚版本" "$BACKEND_PROCESS_PATTERN" "$BACKEND_HEALTH_URL" "$failed_pid"
}

deploy_backend() {
  local pod="$1"
  local archive="$TMP_DIR/backend.tar.gz"

  echo "==> [后端] 打包完整 deeptutor/ 包..."
  tar -czf "$archive" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "$PROJECT_ROOT" deeptutor

  echo "==> [后端] 上传并原子替换..."
  kubectl -n "$NAMESPACE" cp "$archive" "$pod:/tmp/dt-backend.tar.gz" -c "$CONTAINER"
  kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    set -e
    rm -rf /app/deeptutor.hot-deploy.new /app/deeptutor.hot-deploy.bak
    mkdir -p /app/deeptutor.hot-deploy.new
    tar -xzf /tmp/dt-backend.tar.gz -C /app/deeptutor.hot-deploy.new
    rm -f /tmp/dt-backend.tar.gz
    test -f /app/deeptutor.hot-deploy.new/deeptutor/__init__.py
    mv /app/deeptutor /app/deeptutor.hot-deploy.bak
    mv /app/deeptutor.hot-deploy.new/deeptutor /app/deeptutor
    rmdir /app/deeptutor.hot-deploy.new
    chown -R deeptutor:deeptutor /app/deeptutor
  '

  echo "==> [后端] 重启并检查健康状态..."
  local old_pid
  old_pid=$(restart_process "$pod" "$BACKEND_PROCESS_PATTERN")
  if wait_for_service \
    "$pod" "后端" "$BACKEND_PROCESS_PATTERN" "$BACKEND_HEALTH_URL" "$old_pid"; then
    kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- \
      rm -rf /app/deeptutor.hot-deploy.bak
    return 0
  fi

  rollback_backend "$pod"
  return 1
}

prepare_frontend_archive() {
  local stage="$TMP_DIR/frontend"
  local archive="$TMP_DIR/frontend.tar.gz"

  local unsafe_changes
  unsafe_changes=$(cd "$PROJECT_ROOT" && git status --porcelain -- \
    web/package.json \
    web/package-lock.json \
    web/npm-shrinkwrap.json \
    web/pnpm-lock.yaml \
    web/yarn.lock \
    web/bun.lock \
    web/bun.lockb \
    'web/next.config.*')
  if [[ -n "$unsafe_changes" ]]; then
    echo "✗ 前端依赖或 Next 配置已修改，不能安全热部署："
    echo "$unsafe_changes" | sed 's/^/    /'
    echo "  请构建并发布 Linux 目标平台的完整镜像。"
    return 1
  fi

  echo "==> [前端] 本地构建..."
  (
    cd "$PROJECT_ROOT/web"
    npm run build
  )

  test -f "$PROJECT_ROOT/web/.next/standalone/server.js"
  test -d "$PROJECT_ROOT/web/.next/static"
  test -d "$PROJECT_ROOT/web/public"

  echo "==> [前端] 组装完整 standalone 产物..."
  mkdir -p "$stage/.next"
  cp -a "$PROJECT_ROOT/web/.next/standalone/server.js" "$stage/server.js"
  cp -a "$PROJECT_ROOT/web/.next/standalone/.next/." "$stage/.next/"
  cp -a "$PROJECT_ROOT/web/.next/static" "$stage/.next/static"
  cp -a "$PROJECT_ROOT/web/public" "$stage/public"
  tar -czf "$archive" -C "$stage" .

  local size
  size=$(du -h "$archive" | cut -f1)
  echo "    打包大小: $size"
}

rollback_frontend() {
  local pod="$1"
  echo "    正在回滚前端..."
  kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    rm -rf /app/web/.next /app/web/public
    rm -f /app/web/server.js
    mv /app/web.hot-deploy.bak/server.js /app/web/server.js
    mv /app/web.hot-deploy.bak/.next /app/web/.next
    mv /app/web.hot-deploy.bak/public /app/web/public
    rmdir /app/web.hot-deploy.bak
    chown -R deeptutor:deeptutor /app/web/server.js /app/web/.next /app/web/public
  '
  local failed_pid
  failed_pid=$(restart_process "$pod" "$FRONTEND_PROCESS_PATTERN")
  wait_for_service \
    "$pod" "前端回滚版本" "$FRONTEND_PROCESS_PATTERN" "$FRONTEND_HEALTH_URL" "$failed_pid"
}

deploy_frontend() {
  local pod="$1"
  local archive="$TMP_DIR/frontend.tar.gz"

  prepare_frontend_archive

  echo "==> [前端] 上传并原子替换完整运行目录..."
  kubectl -n "$NAMESPACE" cp "$archive" "$pod:/tmp/dt-frontend.tar.gz" -c "$CONTAINER"
  kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    set -e
    rm -rf /app/web.hot-deploy.new /app/web.hot-deploy.bak
    mkdir -p /app/web.hot-deploy.new
    tar -xzf /tmp/dt-frontend.tar.gz -C /app/web.hot-deploy.new
    rm -f /tmp/dt-frontend.tar.gz
    test -f /app/web.hot-deploy.new/server.js
    test -d /app/web.hot-deploy.new/.next/static
    test -d /app/web.hot-deploy.new/public
    test -d /app/web/node_modules
    mkdir -p /app/web.hot-deploy.bak
    mv /app/web/server.js /app/web.hot-deploy.bak/server.js
    mv /app/web/.next /app/web.hot-deploy.bak/.next
    mv /app/web/public /app/web.hot-deploy.bak/public
    mv /app/web.hot-deploy.new/server.js /app/web/server.js
    mv /app/web.hot-deploy.new/.next /app/web/.next
    mv /app/web.hot-deploy.new/public /app/web/public
    rmdir /app/web.hot-deploy.new
    chown -R deeptutor:deeptutor /app/web/server.js /app/web/.next /app/web/public
  '

  echo "==> [前端] 重启并检查 HTTP 健康状态..."
  local old_pid
  old_pid=$(restart_process "$pod" "$FRONTEND_PROCESS_PATTERN")
  if wait_for_service \
    "$pod" "前端" "$FRONTEND_PROCESS_PATTERN" "$FRONTEND_HEALTH_URL" "$old_pid"; then
    kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- \
      rm -rf /app/web.hot-deploy.bak
    return 0
  fi

  rollback_frontend "$pod"
  return 1
}

main() {
  local target="${1:-all}"

  case "$target" in
    backend|frontend|all) ;;
    *)
      echo "用法: $0 [backend|frontend|all]"
      exit 1
      ;;
  esac

  local pod
  pod=$(get_pod)
  if [[ -z "$pod" ]]; then
    echo "✗ 找不到运行中的 Pod ($NAMESPACE/$LABEL)"
    exit 1
  fi
  echo "目标 Pod: $pod"

  case "$target" in
    backend)
      deploy_backend "$pod"
      ;;
    frontend)
      deploy_frontend "$pod"
      ;;
    all)
      deploy_backend "$pod"
      echo ""
      deploy_frontend "$pod"
      ;;
  esac

  echo ""
  echo "✓ 热部署完成"
}

main "$@"
