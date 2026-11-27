#!/bin/bash
# Learning-loop acceptance: a fresh synthetic stage, a scripted model and a real
# browser walk the main learner path end to end, then everything is torn down.
#
#   bash scripts/learning_e2e/run.sh            # dev frontend (default)
#   KEEP=1 bash scripts/learning_e2e/run.sh     # leave the stage running
#
# Ports: backend 18181, frontend 13882, model 18190 (override BP/FP/MP).
# The stage copies deeptutor/ and web/ (not data/), so real user data is never
# read. Screenshots and the model request log stay under the printed stage dir.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
here="$repo/scripts/learning_e2e"
stage=$(mktemp -d /tmp/deeptutor-learning-e2e.XXXXXX)
bp=${BP:-18181}; fp=${FP:-13882}; mp=${MP:-18190}
context_path=${E2E_CONTEXT_PATH:-}
[[ -z "$context_path" || "$context_path" =~ ^/[a-zA-Z0-9_-]+$ ]] || {
  echo "E2E_CONTEXT_PATH must be empty or a single path segment." >&2; exit 2
}
node=$(command -v node); py="$repo/.venv/bin/python"
for port in "$bp" "$fp" "$mp"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port $port is in use; set BP/FP/MP." >&2; exit 2
  fi
done
echo "Stage: $stage"
mkdir -p "$stage/web" "$stage/deeptutor" "$stage/home" "$stage/out"
rsync -a --no-links --exclude=node_modules --exclude='.next*' --exclude='.env*' \
  --exclude=test-results --exclude=playwright-report "$repo/web/" "$stage/web/"
rsync -a --no-links --exclude='.env*' --exclude=__pycache__ "$repo/deeptutor/" "$stage/deeptutor/"
ln -s "$repo/web/node_modules" "$stage/web/node_modules"
settings="$stage/data/user/settings"; mkdir -p "$settings"
echo '{"enabled": true, "username": "", "password_hash": "", "cookie_secure": false, "private_login_hosts": []}' > "$settings/auth.json"
echo "{\"backend_port\": $bp, \"frontend_port\": $fp, \"backend_workers\": 1, \"version_check_enabled\": false}" > "$settings/system.json"
printf 'system:\n  language: zh\nlogging:\n  level: WARNING\ntools:\n  web_search:\n    enabled: false\n' > "$settings/main.yaml"
cat > "$settings/model_catalog.json" <<JSON
{"version":1,"services":{"llm":{"active_profile_id":"mock","active_model_id":"mock-m","profiles":[{"id":"mock","name":"Mock","binding":"openai","base_url":"http://127.0.0.1:$mp/v1","api_key":"sk-mock","api_version":"","extra_headers":{},"models":[{"id":"mock-m","name":"mock-model","model":"mock-model"}]}]},"embedding":{"active_profile_id":null,"active_model_id":null,"profiles":[]},"search":{"active_profile_id":null,"profiles":[]}}}
JSON
env_run=(/usr/bin/env -i PATH="$(dirname "$node"):/usr/bin:/bin:/usr/sbin:/sbin" HOME="$stage/home"
  TMPDIR="$stage" PYTHONPATH="$stage" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
  DEEPTUTOR_HOME="$stage" AUTH_ENABLED=true AUTH_COOKIE_SECURE=false DEEPTUTOR_AUTH_ENABLED=true
  NEXT_PUBLIC_AUTH_ENABLED=true BACKEND_PORT="$bp" FRONTEND_PORT="$fp"
  DEEPTUTOR_API_BASE_URL="http://127.0.0.1:$bp" NEXT_PUBLIC_API_BASE="http://127.0.0.1:$bp"
  NEXT_PUBLIC_CONTEXT_PATH="$context_path" CONTEXT_PATH="$context_path" NEXT_TELEMETRY_DISABLED=1
  DEEPTUTOR_VERSION_CHECK_ENABLED=false DEEPTUTOR_TURN_COORDINATION_BACKEND=memory NO_PROXY='*')
pids=()
cleanup() {
  if [[ "${KEEP:-}" == 1 ]]; then echo "KEEP=1: stage left running ($stage)"; return; fi
  for pid in "${pids[@]}"; do pkill -TERM -P "$pid" 2>/dev/null || true; kill "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
cd "$stage"
"$py" -I "$here/mock_llm.py" "$mp" "$stage/model-requests.jsonl" "$stage" > "$stage/model.log" 2>&1 & pids+=($!)
"${env_run[@]}" "$py" -B -c "from deeptutor.services.setup import init_user_directories; init_user_directories()"
"${env_run[@]}" "$node" web/scripts/copy-pdfjs-assets.mjs >/dev/null
"${env_run[@]}" "$py" -B -m uvicorn deeptutor.api.main:app --host 127.0.0.1 --port "$bp" --workers 1 > "$stage/backend.log" 2>&1 & pids+=($!)
"${env_run[@]}" "$node" web/scripts/dev.mjs --webpack --hostname 127.0.0.1 --port "$fp" > "$stage/frontend.log" 2>&1 & pids+=($!)
for _ in $(seq 1 80); do
  if curl -fs "http://127.0.0.1:$bp/health/ready" >/dev/null && curl -fs "http://127.0.0.1:$fp$context_path/api/auth/is_first_user" | grep -q true; then
    ready=1; break
  fi
  sleep 3
done
[[ "${ready:-}" == 1 ]] || { echo "Stage did not become ready; see $stage/*.log" >&2; exit 1; }
STAGE="$stage" FRONT="http://127.0.0.1:$fp$context_path" "$node" "${SCENARIO:-$here/scenario.mjs}"
