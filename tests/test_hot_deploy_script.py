from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "hot-deploy.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")


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
    assert "git status --porcelain" not in SCRIPT
    assert "frontend_runtime_manifest" in SCRIPT
    assert "/app/web/.hot-deploy-runtime-inputs.sha256" in SCRIPT
    assert 'cmp -s "$local_manifest" "$pod_manifest"' in SCRIPT
    assert "package.json" in SCRIPT
    assert "package-lock.json" in SCRIPT
    assert "next.config.*" in SCRIPT
    assert "请构建并发布 Linux 目标平台的完整镜像" in SCRIPT


def test_image_embeds_the_frontend_runtime_input_manifest() -> None:
    assert "sha256sum" in DOCKERFILE
    assert "> /tmp/frontend-runtime-inputs.sha256" in DOCKERFILE
    assert (
        "COPY --from=frontend-builder /tmp/frontend-runtime-inputs.sha256 "
        "./web/.hot-deploy-runtime-inputs.sha256"
    ) in DOCKERFILE


def test_frontend_rollback_survives_until_stable_http_readiness() -> None:
    readiness = SCRIPT.index('"$pod" "前端" "$FRONTEND_PROCESS_PATTERN"')
    commit = SCRIPT.index("commit_frontend()", readiness)

    assert "HEALTH_STABLE_CHECKS=5" in SCRIPT
    assert "curl -sS --max-time 2" in SCRIPT
    assert '"$pid" != "$previous_pid"' in SCRIPT
    assert readiness < commit


def test_restart_failures_enter_component_rollback_paths() -> None:
    assert 'if ! old_pid=$(restart_process "$pod" "$BACKEND_PROCESS_PATTERN")' in SCRIPT
    assert 'if ! old_pid=$(restart_process "$pod" "$FRONTEND_PROCESS_PATTERN")' in SCRIPT
    assert 'echo "    ✗ 后端重启命令失败"\n    rollback_backend "$pod"' in SCRIPT
    assert 'echo "    ✗ 前端重启命令失败"\n    rollback_frontend "$pod"' in SCRIPT


def test_backups_are_owned_by_the_current_deployment() -> None:
    assert 'DEPLOY_ID="$(date +%s)-$$-$RANDOM"' in SCRIPT
    assert "/app/deeptutor.hot-deploy.bak/.hot-deploy-owner" in SCRIPT
    assert "/app/web.hot-deploy.bak/.hot-deploy-owner" in SCRIPT
    assert "备份不属于本次部署，拒绝自动回滚" in SCRIPT


def test_all_mode_prepares_both_artifacts_and_coordinates_rollback() -> None:
    deploy_all = SCRIPT[SCRIPT.index("deploy_all()") : SCRIPT.index("main()")]
    backend_activation = deploy_all.index('activate_backend "$pod"')
    frontend_activation = deploy_all.index('activate_frontend "$pod"')
    frontend_commit = deploy_all.index('commit_frontend "$pod"')
    backend_commit = deploy_all.index('commit_backend "$pod"')

    assert deploy_all.index("prepare_backend_archive") < backend_activation
    assert deploy_all.index('prepare_frontend_archive "$pod"') < backend_activation
    assert deploy_all.index('upload_backend_archive "$pod"') < backend_activation
    assert deploy_all.index('upload_frontend_archive "$pod"') < backend_activation
    assert backend_activation < frontend_activation < frontend_commit
    assert backend_activation < frontend_activation < backend_commit
    assert 'if ! rollback_backend "$pod"' in deploy_all
