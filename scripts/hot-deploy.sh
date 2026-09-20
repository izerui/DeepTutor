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
DEPLOY_ID="$(date +%s)-$$-$RANDOM"

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

prepare_backend_archive() {
  local archive="$TMP_DIR/backend.tar.gz"

  echo "==> [后端] 打包完整 deeptutor/ 包..."
  tar -czf "$archive" \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -C "$PROJECT_ROOT" deeptutor
}

upload_backend_archive() {
  local pod="$1"
  kubectl -n "$NAMESPACE" cp \
    "$TMP_DIR/backend.tar.gz" "$pod:/tmp/dt-backend.tar.gz" -c "$CONTAINER"
}

rollback_backend() {
  local pod="$1"
  echo "    正在回滚后端..."
  local result
  if ! result=$(kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
      set -e
      if [[ ! -d /app/deeptutor.hot-deploy.bak ]]; then
        printf "no-backup"
        exit 0
      fi
      if [[ "$(cat /app/deeptutor.hot-deploy.bak/.hot-deploy-owner 2>/dev/null || true)" != "$1" ]]; then
        printf "foreign-backup"
        exit 0
      fi
      rm -f /app/deeptutor.hot-deploy.bak/.hot-deploy-owner
      rm -rf /app/deeptutor
      mv /app/deeptutor.hot-deploy.bak /app/deeptutor
      chown -R deeptutor:deeptutor /app/deeptutor
      printf "restored"
    ' _ "$DEPLOY_ID"); then
    echo "    ✗ 无法恢复后端备份"
    return 1
  fi
  if [[ "$result" == "no-backup" ]]; then
    echo "    后端尚未替换，无需回滚"
    return 0
  fi
  if [[ "$result" == "foreign-backup" ]]; then
    echo "    ✗ 后端备份不属于本次部署，拒绝自动回滚"
    return 1
  fi

  local failed_pid=""
  if ! failed_pid=$(restart_process "$pod" "$BACKEND_PROCESS_PATTERN"); then
    echo "    ✗ 后端文件已恢复，但重启命令失败"
    return 1
  fi
  if ! wait_for_service \
    "$pod" "后端回滚版本" "$BACKEND_PROCESS_PATTERN" "$BACKEND_HEALTH_URL" "$failed_pid"; then
    echo "    ✗ 后端文件已恢复，但回滚版本未通过健康检查"
    return 1
  fi
}

activate_backend() {
  local pod="$1"

  echo "==> [后端] 原子替换..."
  if ! kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    set -Eeuo pipefail
    backup_created=0
    rollback_on_error() {
      if ((backup_created)); then
        rm -f /app/deeptutor.hot-deploy.bak/.hot-deploy-owner
        rm -rf /app/deeptutor
        mv /app/deeptutor.hot-deploy.bak /app/deeptutor
        chown -R deeptutor:deeptutor /app/deeptutor
      fi
    }
    trap rollback_on_error ERR
    rm -rf /app/deeptutor.hot-deploy.new
    test ! -e /app/deeptutor.hot-deploy.bak
    mkdir -p /app/deeptutor.hot-deploy.new
    tar -xzf /tmp/dt-backend.tar.gz -C /app/deeptutor.hot-deploy.new
    rm -f /tmp/dt-backend.tar.gz
    test -f /app/deeptutor.hot-deploy.new/deeptutor/__init__.py
    mv /app/deeptutor /app/deeptutor.hot-deploy.bak
    backup_created=1
    printf "%s" "$1" > /app/deeptutor.hot-deploy.bak/.hot-deploy-owner
    mv /app/deeptutor.hot-deploy.new/deeptutor /app/deeptutor
    rmdir /app/deeptutor.hot-deploy.new
    chown -R deeptutor:deeptutor /app/deeptutor
    trap - ERR
  ' _ "$DEPLOY_ID"; then
    echo "    ✗ 后端替换失败"
    rollback_backend "$pod" || true
    return 1
  fi

  echo "==> [后端] 重启并检查健康状态..."
  local old_pid=""
  if ! old_pid=$(restart_process "$pod" "$BACKEND_PROCESS_PATTERN"); then
    echo "    ✗ 后端重启命令失败"
    rollback_backend "$pod" || true
    return 1
  fi
  if ! wait_for_service \
    "$pod" "后端" "$BACKEND_PROCESS_PATTERN" "$BACKEND_HEALTH_URL" "$old_pid"; then
    rollback_backend "$pod" || true
    return 1
  fi
}

commit_backend() {
  local pod="$1"
  if ! kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
      [[ "$(cat /app/deeptutor.hot-deploy.bak/.hot-deploy-owner 2>/dev/null || true)" == "$1" ]]
      rm -rf /app/deeptutor.hot-deploy.bak
    ' _ "$DEPLOY_ID"; then
    echo "    ⚠ 后端已部署，但清理回滚备份失败"
  fi
}

frontend_runtime_manifest() {
  (
    cd "$PROJECT_ROOT/web"
    local file
    for file in \
      package.json \
      package-lock.json \
      npm-shrinkwrap.json \
      pnpm-lock.yaml \
      yarn.lock \
      bun.lock \
      bun.lockb \
      next.config.*; do
      if [[ -f "$file" ]]; then
        shasum -a 256 "$file"
      fi
    done
  ) | LC_ALL=C sort
}

validate_frontend_runtime_inputs() {
  local pod="$1"
  local local_manifest="$TMP_DIR/frontend-runtime-inputs.local.sha256"
  local pod_manifest="$TMP_DIR/frontend-runtime-inputs.pod.sha256"

  frontend_runtime_manifest > "$local_manifest"
  if ! kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- \
    cat /app/web/.hot-deploy-runtime-inputs.sha256 > "$pod_manifest"; then
    echo "✗ Pod 镜像不包含前端运行输入基线，不能安全热部署。"
    echo "  请先构建并发布包含最新热部署支持的完整镜像。"
    return 1
  fi

  if ! cmp -s "$local_manifest" "$pod_manifest"; then
    echo "✗ 本地前端依赖或 Next 配置与 Pod 镜像不一致，不能安全热部署："
    diff -u "$pod_manifest" "$local_manifest" | sed 's/^/    /' || true
    echo "  请构建并发布 Linux 目标平台的完整镜像。"
    return 1
  fi
}

prepare_frontend_archive() {
  local pod="$1"
  local stage="$TMP_DIR/frontend"
  local archive="$TMP_DIR/frontend.tar.gz"

  validate_frontend_runtime_inputs "$pod"

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

upload_frontend_archive() {
  local pod="$1"
  kubectl -n "$NAMESPACE" cp \
    "$TMP_DIR/frontend.tar.gz" "$pod:/tmp/dt-frontend.tar.gz" -c "$CONTAINER"
}

rollback_frontend() {
  local pod="$1"
  echo "    正在回滚前端..."
  local result
  if ! result=$(kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
      set -e
      if [[ ! -d /app/web.hot-deploy.bak ]]; then
        printf "no-backup"
        exit 0
      fi
      if [[ "$(cat /app/web.hot-deploy.bak/.hot-deploy-owner 2>/dev/null || true)" != "$1" ]]; then
        printf "foreign-backup"
        exit 0
      fi
      rm -rf /app/web/.next /app/web/public
      rm -f /app/web/server.js
      cp -a /app/web.hot-deploy.bak/server.js /app/web/server.js
      cp -a /app/web.hot-deploy.bak/.next /app/web/.next
      cp -a /app/web.hot-deploy.bak/public /app/web/public
      rm -rf /app/web.hot-deploy.bak
      chown -R deeptutor:deeptutor /app/web/server.js /app/web/.next /app/web/public
      printf "restored"
    ' _ "$DEPLOY_ID"); then
    echo "    ✗ 无法恢复前端备份"
    return 1
  fi
  if [[ "$result" == "no-backup" ]]; then
    echo "    前端尚未替换，无需回滚"
    return 0
  fi
  if [[ "$result" == "foreign-backup" ]]; then
    echo "    ✗ 前端备份不属于本次部署，拒绝自动回滚"
    return 1
  fi

  local failed_pid=""
  if ! failed_pid=$(restart_process "$pod" "$FRONTEND_PROCESS_PATTERN"); then
    echo "    ✗ 前端文件已恢复，但重启命令失败"
    return 1
  fi
  if ! wait_for_service \
    "$pod" "前端回滚版本" "$FRONTEND_PROCESS_PATTERN" "$FRONTEND_HEALTH_URL" "$failed_pid"; then
    echo "    ✗ 前端文件已恢复，但回滚版本未通过健康检查"
    return 1
  fi
}

activate_frontend() {
  local pod="$1"

  echo "==> [前端] 原子替换完整运行目录..."
  if ! kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
    set -Eeuo pipefail
    backup_created=0
    rollback_on_error() {
      if ((backup_created)); then
        rm -rf /app/web/.next /app/web/public
        rm -f /app/web/server.js
        cp -a /app/web.hot-deploy.bak/server.js /app/web/server.js
        cp -a /app/web.hot-deploy.bak/.next /app/web/.next
        cp -a /app/web.hot-deploy.bak/public /app/web/public
        rm -rf /app/web.hot-deploy.bak
        chown -R deeptutor:deeptutor /app/web/server.js /app/web/.next /app/web/public
      fi
    }
    trap rollback_on_error ERR
    rm -rf /app/web.hot-deploy.new
    test ! -e /app/web.hot-deploy.bak
    mkdir -p /app/web.hot-deploy.new
    tar -xzf /tmp/dt-frontend.tar.gz -C /app/web.hot-deploy.new
    rm -f /tmp/dt-frontend.tar.gz
    test -f /app/web.hot-deploy.new/server.js
    test -d /app/web.hot-deploy.new/.next/static
    test -d /app/web.hot-deploy.new/public
    test -d /app/web/node_modules
    mkdir -p /app/web.hot-deploy.bak
    cp -a /app/web/server.js /app/web.hot-deploy.bak/server.js
    cp -a /app/web/.next /app/web.hot-deploy.bak/.next
    cp -a /app/web/public /app/web.hot-deploy.bak/public
    backup_created=1
    printf "%s" "$1" > /app/web.hot-deploy.bak/.hot-deploy-owner
    rm -rf /app/web/.next /app/web/public
    rm -f /app/web/server.js
    mv /app/web.hot-deploy.new/server.js /app/web/server.js
    mv /app/web.hot-deploy.new/.next /app/web/.next
    mv /app/web.hot-deploy.new/public /app/web/public
    rmdir /app/web.hot-deploy.new
    chown -R deeptutor:deeptutor /app/web/server.js /app/web/.next /app/web/public
    trap - ERR
  ' _ "$DEPLOY_ID"; then
    echo "    ✗ 前端替换失败"
    rollback_frontend "$pod" || true
    return 1
  fi

  echo "==> [前端] 重启并检查 HTTP 健康状态..."
  local old_pid=""
  if ! old_pid=$(restart_process "$pod" "$FRONTEND_PROCESS_PATTERN"); then
    echo "    ✗ 前端重启命令失败"
    rollback_frontend "$pod" || true
    return 1
  fi
  if ! wait_for_service \
    "$pod" "前端" "$FRONTEND_PROCESS_PATTERN" "$FRONTEND_HEALTH_URL" "$old_pid"; then
    rollback_frontend "$pod" || true
    return 1
  fi
}

commit_frontend() {
  local pod="$1"
  if ! kubectl -n "$NAMESPACE" exec "$pod" -c "$CONTAINER" -- bash -c '
      [[ "$(cat /app/web.hot-deploy.bak/.hot-deploy-owner 2>/dev/null || true)" == "$1" ]]
      rm -rf /app/web.hot-deploy.bak
    ' _ "$DEPLOY_ID"; then
    echo "    ⚠ 前端已部署，但清理回滚备份失败"
  fi
}

deploy_backend() {
  local pod="$1"
  prepare_backend_archive
  echo "==> [后端] 上传产物..."
  upload_backend_archive "$pod"
  activate_backend "$pod"
  commit_backend "$pod"
}

deploy_frontend() {
  local pod="$1"
  prepare_frontend_archive "$pod"
  echo "==> [前端] 上传产物..."
  upload_frontend_archive "$pod"
  activate_frontend "$pod"
  commit_frontend "$pod"
}

deploy_all() {
  local pod="$1"

  prepare_backend_archive
  prepare_frontend_archive "$pod"

  echo "==> [全部] 在替换服务前上传所有产物..."
  upload_backend_archive "$pod"
  upload_frontend_archive "$pod"

  if ! activate_backend "$pod"; then
    return 1
  fi

  if ! activate_frontend "$pod"; then
    echo "==> [全部] 前端部署失败，回滚已部署的后端..."
    if ! rollback_backend "$pod"; then
      echo "✗ 前端部署失败，且后端协调回滚失败，需要人工介入"
    fi
    return 1
  fi

  commit_frontend "$pod"
  commit_backend "$pod"
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
      deploy_all "$pod"
      ;;
  esac

  echo ""
  echo "✓ 热部署完成"
}

main "$@"
