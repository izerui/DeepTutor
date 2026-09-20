from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "hot-deploy.sh").read_text(encoding="utf-8")


def test_backend_deploys_an_exact_package_and_keeps_a_rollback() -> None:
    assert "git diff --name-only" not in SCRIPT
    assert '-C "$PROJECT_ROOT" deeptutor' in SCRIPT
    assert "/app/deeptutor.hot-deploy.bak" in SCRIPT
    assert '"$pod" "后端" "$BACKEND_PROCESS_PATTERN"' in SCRIPT


def test_frontend_preserves_target_platform_node_modules() -> None:
    assert 'cp -a "$PROJECT_ROOT/web/.next/standalone/server.js"' in SCRIPT
    assert 'cp -a "$PROJECT_ROOT/web/.next/standalone/.next/."' in SCRIPT
    assert 'cp -a "$PROJECT_ROOT/web/.next/static" "$stage/.next/static"' in SCRIPT
    assert 'cp -a "$PROJECT_ROOT/web/public" "$stage/public"' in SCRIPT
    assert "test -f /app/web.hot-deploy.new/server.js" in SCRIPT
    assert "test -d /app/web/node_modules" in SCRIPT
    assert "mv /app/web/node_modules" not in SCRIPT


def test_frontend_rejects_dependency_and_next_config_changes() -> None:
    assert "git status --porcelain" in SCRIPT
    assert "web/package.json" in SCRIPT
    assert "web/package-lock.json" in SCRIPT
    assert "'web/next.config.*'" in SCRIPT
    assert "请构建并发布 Linux 目标平台的完整镜像" in SCRIPT


def test_frontend_rollback_survives_until_stable_http_readiness() -> None:
    readiness = SCRIPT.index('"$pod" "前端" "$FRONTEND_PROCESS_PATTERN"')
    discard_backup = SCRIPT.index("rm -rf /app/web.hot-deploy.bak", readiness)

    assert "HEALTH_STABLE_CHECKS=5" in SCRIPT
    assert "curl -sS --max-time 2" in SCRIPT
    assert '"$pid" != "$previous_pid"' in SCRIPT
    assert readiness < discard_backup
