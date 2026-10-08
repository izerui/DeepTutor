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


# ---------------------------------------------------------------------------
# Context-path safety: text-level assertions
# ---------------------------------------------------------------------------


def test_frontend_health_url_uses_context_path() -> None:
    assert 'CONTEXT_PATH="${NEXT_PUBLIC_CONTEXT_PATH:-}"' in SCRIPT
    assert 'FRONTEND_HEALTH_URL="http://127.0.0.1:3782${CONTEXT_PATH}/login"' in SCRIPT


def test_basepath_parsing_requires_field_and_string_type() -> None:
    """Both the pod and local manifest parsers must reject missing or
    non-string basePath instead of silently treating it as root."""
    assert "'basePath' not in m" in SCRIPT
    assert "not isinstance(v,str)" in SCRIPT
    assert SCRIPT.count("'basePath' not in m") >= 2
    assert SCRIPT.count("not isinstance(v,str)") >= 2


def test_local_build_receives_context_path_env() -> None:
    assert 'NEXT_PUBLIC_CONTEXT_PATH="${CONTEXT_PATH}" npm run build' in SCRIPT


# ---------------------------------------------------------------------------
# Context-path safety: behavioural assertions via subprocess
# ---------------------------------------------------------------------------

import json
import os
import subprocess
import textwrap
import tempfile


def _run_basepath_check(
    *,
    pod_manifest: dict | None = None,
    pod_manifest_missing: bool = False,
    kubectl_connrefused: bool = False,
    local_manifest: dict | None = None,
    context_path: str = "",
) -> subprocess.CompletedProcess:
    """Execute the basePath validation + build portion of hot-deploy.sh using
    fake kubectl and npm that exercise the real parsing code.

    - pod_manifest: JSON dict to write as the pod's routes-manifest.json.
      The fake kubectl intercepts the python3 -c call and redirects the
      file path to a local temp file, so the real parsing code runs.
    - pod_manifest_missing: if True, the pod manifest file does not exist.
    - kubectl_connrefused: if True, kubectl itself fails (connection error).
    - local_manifest: JSON dict for the local build's routes-manifest.json.
    - context_path: NEXT_PUBLIC_CONTEXT_PATH value.
    """
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        web_next = project / "web" / ".next"
        web_next.mkdir(parents=True)
        standalone = web_next / "standalone" / ".next"
        standalone.mkdir(parents=True)
        (web_next / "standalone" / "server.js").write_text("")
        (web_next / "static").mkdir()
        (project / "web" / "public").mkdir()

        if local_manifest is not None:
            (web_next / "routes-manifest.json").write_text(
                json.dumps(local_manifest)
            )

        # Pod manifest — written to a temp file that fake kubectl redirects to
        pod_manifest_path = Path(tmp) / "pod-routes-manifest.json"
        if pod_manifest is not None and not pod_manifest_missing:
            pod_manifest_path.write_text(json.dumps(pod_manifest))

        # Fake kubectl: intercepts `kubectl ... exec ... -- python3 -c "..."`,
        # rewrites the file path in the Python code to use the local temp file,
        # then executes the real parsing code.
        if kubectl_connrefused:
            fake_kubectl_code = '#!/bin/bash\necho "connection refused" >&2\nexit 1\n'
        else:
            fake_kubectl_code = textwrap.dedent(f"""\
                #!/bin/bash
                # Find the python3 -c argument in the kubectl exec call
                for arg in "$@"; do
                    if [[ "$arg" == *"import json"* ]]; then
                        # Replace the pod manifest path with our local temp file
                        modified=$(echo "$arg" | sed "s|/app/web/.next/routes-manifest.json|{pod_manifest_path}|g")
                        exec python3 -c "$modified"
                    fi
                done
                echo "kubectl: unrecognized call" >&2
                exit 1
            """)
        fake_kubectl = Path(tmp) / "kubectl"
        fake_kubectl.write_text(fake_kubectl_code)
        fake_kubectl.chmod(0o755)

        # Fake npm: records NEXT_PUBLIC_CONTEXT_PATH to a file for assertion
        npm_env_log = Path(tmp) / "npm-env.log"
        fake_npm = Path(tmp) / "npm"
        fake_npm.write_text(textwrap.dedent(f"""\
            #!/bin/bash
            echo "NEXT_PUBLIC_CONTEXT_PATH=${{NEXT_PUBLIC_CONTEXT_PATH:-}}" > "{npm_env_log}"
            exit 0
        """))
        fake_npm.chmod(0o755)

        # Extract validate + build portion into a testable script
        src = SCRIPT
        start = src.index("# Detect the basePath baked into")
        end = src.index('echo "==> [前端] 组装完整 standalone 产物..."')
        chunk = src[start:end]
        chunk = chunk.replace("$PROJECT_ROOT", str(project))

        test_script = textwrap.dedent(f"""\
            #!/bin/bash
            set -euo pipefail
            export PATH="{tmp}:$PATH"
            NAMESPACE="test"
            LABEL="app=test"
            CONTAINER="test"
            CONTEXT_PATH="{context_path}"
            PROJECT_ROOT="{project}"

            validate_frontend_runtime_inputs() {{ true; }}

            prepare_frontend_archive() {{
                local pod="$1"
                local stage="{tmp}/stage"
                local archive="{tmp}/frontend.tar.gz"

                validate_frontend_runtime_inputs "$pod"

        """)
        test_script += "        " + chunk.replace("\n", "\n        ")
        test_script += textwrap.dedent(f"""
                echo "REACHED_PACKAGING"
            }}

            prepare_frontend_archive "test-pod"
        """)

        script_path = Path(tmp) / "test.sh"
        script_path.write_text(test_script)
        script_path.chmod(0o755)

        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"},
        )
        # Attach npm env log for callers to inspect
        result.npm_env_log = npm_env_log.read_text().strip() if npm_env_log.exists() else ""  # type: ignore[attr-defined]
        return result


class TestBasepathBehavioural:
    """Run the actual shell code with fake kubectl/npm to verify abort vs
    proceed behavior.  The fake kubectl executes the real Python parsing
    code against temp manifest files."""

    # --- Abort cases ---

    def test_kubectl_connection_failure_aborts(self) -> None:
        r = _run_basepath_check(
            kubectl_connrefused=True,
            local_manifest={"basePath": ""},
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout
        assert "无法读取" in r.stdout

    def test_pod_manifest_file_missing_aborts(self) -> None:
        r = _run_basepath_check(
            pod_manifest_missing=True,
            local_manifest={"basePath": ""},
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout

    def test_pod_manifest_missing_basepath_key_aborts(self) -> None:
        """Pod manifest is {} — basePath key absent. Must not fall back to ''."""
        r = _run_basepath_check(
            pod_manifest={},
            local_manifest={"basePath": ""},
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout

    def test_pod_manifest_null_basepath_aborts(self) -> None:
        """Pod manifest has basePath: null — not a string."""
        r = _run_basepath_check(
            pod_manifest={"basePath": None},
            local_manifest={"basePath": ""},
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout

    def test_pod_manifest_numeric_basepath_aborts(self) -> None:
        """Pod manifest has basePath: 42 — not a string."""
        r = _run_basepath_check(
            pod_manifest={"basePath": 42},
            local_manifest={"basePath": ""},
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout

    def test_prefix_mismatch_aborts(self) -> None:
        r = _run_basepath_check(
            pod_manifest={"basePath": "/kaoyan"},
            local_manifest={"basePath": "/kaoyan"},
            context_path="",  # local env says root, pod says /kaoyan
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout
        assert "不匹配" in r.stdout

    def test_local_build_basepath_mismatch_aborts(self) -> None:
        r = _run_basepath_check(
            pod_manifest={"basePath": "/kaoyan"},
            local_manifest={"basePath": ""},  # build produced root instead of /kaoyan
            context_path="/kaoyan",
        )
        assert r.returncode != 0
        assert "REACHED_PACKAGING" not in r.stdout
        assert "本地构建产物" in r.stdout

    # --- Success cases ---

    def test_matching_prefix_reaches_packaging(self) -> None:
        r = _run_basepath_check(
            pod_manifest={"basePath": "/kaoyan"},
            local_manifest={"basePath": "/kaoyan"},
            context_path="/kaoyan",
        )
        assert "REACHED_PACKAGING" in r.stdout, f"stdout: {r.stdout}\nstderr: {r.stderr}"

    def test_matching_root_reaches_packaging(self) -> None:
        r = _run_basepath_check(
            pod_manifest={"basePath": ""},
            local_manifest={"basePath": ""},
            context_path="",
        )
        assert "REACHED_PACKAGING" in r.stdout, f"stdout: {r.stdout}\nstderr: {r.stderr}"

    def test_npm_build_receives_context_path_env(self) -> None:
        """The npm run build command must receive NEXT_PUBLIC_CONTEXT_PATH
        matching the pod's basePath."""
        r = _run_basepath_check(
            pod_manifest={"basePath": "/kaoyan"},
            local_manifest={"basePath": "/kaoyan"},
            context_path="/kaoyan",
        )
        assert "REACHED_PACKAGING" in r.stdout
        assert r.npm_env_log == "NEXT_PUBLIC_CONTEXT_PATH=/kaoyan"  # type: ignore[attr-defined]

    def test_npm_build_receives_empty_context_path_for_root(self) -> None:
        r = _run_basepath_check(
            pod_manifest={"basePath": ""},
            local_manifest={"basePath": ""},
            context_path="",
        )
        assert "REACHED_PACKAGING" in r.stdout
        assert r.npm_env_log == "NEXT_PUBLIC_CONTEXT_PATH="  # type: ignore[attr-defined]


class TestHealthCheckUsesContextPath:
    """Verify that activate_frontend and rollback_frontend pass the
    context-path-prefixed health URL to wait_for_service, and that this
    matches the prefix npm received during build."""

    @staticmethod
    def _run_activate_or_rollback(
        *,
        context_path: str,
        run_rollback: bool = False,
    ) -> tuple[subprocess.CompletedProcess, str, str]:
        """Execute activate_frontend (or rollback_frontend) from hot-deploy.sh
        with a fake kubectl that records the health URL passed to
        wait_for_service, and a fake npm that records NEXT_PUBLIC_CONTEXT_PATH.

        Returns (result, health_urls_logged, npm_env_logged).
        """
        with tempfile.TemporaryDirectory() as tmp:
            health_log = Path(tmp) / "health-urls.log"
            health_log.write_text("")
            npm_env_log = Path(tmp) / "npm-env.log"
            npm_env_log.write_text("")

            # Pod manifest for basePath check
            pod_manifest_path = Path(tmp) / "pod-routes-manifest.json"
            pod_manifest_path.write_text(json.dumps({"basePath": context_path}))

            # Local project with build output
            project = Path(tmp) / "project"
            web_next = project / "web" / ".next"
            web_next.mkdir(parents=True)
            standalone = web_next / "standalone" / ".next"
            standalone.mkdir(parents=True)
            (web_next / "standalone" / "server.js").write_text("")
            (web_next / "static").mkdir()
            (project / "web" / "public").mkdir()
            (web_next / "routes-manifest.json").write_text(
                json.dumps({"basePath": context_path})
            )

            # Fake kubectl that dispatches based on the bash -c script content
            fake_kubectl = Path(tmp) / "kubectl"
            fake_kubectl.write_text(textwrap.dedent(f"""\
                #!/bin/bash
                # Join all args to detect which operation this is
                all_args="$*"

                # basePath read via python3 — redirect to local manifest
                for arg in "$@"; do
                    if [[ "$arg" == *"import json"* ]]; then
                        modified=$(echo "$arg" | sed "s|/app/web/.next/routes-manifest.json|{pod_manifest_path}|g")
                        exec python3 -c "$modified"
                    fi
                done

                # wait_for_service: pgrep + curl — record health URL, return success
                if [[ "$all_args" == *"pgrep"*"curl"* ]]; then
                    # The health URL is the last positional arg passed to bash -c
                    # Script: bash -c '...' _ "$process_pattern" "$health_url"
                    health_url="${{@: -1}}"
                    echo "$health_url" >> "{health_log}"
                    echo "12345 200"
                    exit 0
                fi

                # restart_process: pgrep + kill — return a fake old PID
                if [[ "$all_args" == *"pgrep"*"kill"* ]]; then
                    echo "11111"
                    exit 0
                fi

                # activate_frontend: rm + cp + chown — just succeed
                if [[ "$all_args" == *"hot-deploy"*"chown"* ]]; then
                    exit 0
                fi

                # rollback_frontend: rm + cp restore — return "restored"
                if [[ "$all_args" == *"hot-deploy.bak"*"cp -a"* ]]; then
                    echo "restored"
                    exit 0
                fi

                # commit_frontend: rm backup
                if [[ "$all_args" == *"hot-deploy.bak"* ]]; then
                    exit 0
                fi

                # cp (upload archive)
                if [[ "$1" == *"-n"* && "$2" == *"cp"* ]] || [[ "$all_args" == *"cp "* ]]; then
                    exit 0
                fi

                # Fallback — unknown kubectl call
                echo "fake-kubectl: unhandled call: $all_args" >&2
                exit 0
            """))
            fake_kubectl.chmod(0o755)

            # Fake npm
            fake_npm = Path(tmp) / "npm"
            fake_npm.write_text(textwrap.dedent(f"""\
                #!/bin/bash
                echo "NEXT_PUBLIC_CONTEXT_PATH=${{NEXT_PUBLIC_CONTEXT_PATH:-}}" > "{npm_env_log}"
                exit 0
            """))
            fake_npm.chmod(0o755)

            # Build a test script that sources the relevant functions from
            # hot-deploy.sh and calls activate_frontend or rollback_frontend.
            src = SCRIPT

            test_script = textwrap.dedent(f"""\
                #!/bin/bash
                set -euo pipefail
                export PATH="{tmp}:$PATH"
                NAMESPACE="test"
                LABEL="app=test"
                CONTAINER="test"
                DEPLOY_ID="test-deploy-1"
                CONTEXT_PATH="{context_path}"
                FRONTEND_PROCESS_PATTERN='^next-server'
                FRONTEND_HEALTH_URL="http://127.0.0.1:3782${{CONTEXT_PATH}}/login"
                HEALTH_ATTEMPTS=3
                HEALTH_STABLE_CHECKS=1
                PROJECT_ROOT="{project}"

            """)

            # Extract functions we need: wait_for_service, restart_process,
            # rollback_frontend, activate_frontend
            for func_name in [
                "wait_for_service",
                "restart_process",
                "rollback_frontend",
                "activate_frontend",
            ]:
                func_start = src.index(f"{func_name}()" + " {")
                # Find the closing brace at column 0
                brace_depth = 0
                func_end = func_start
                for i, ch in enumerate(src[func_start:], func_start):
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            func_end = i + 1
                            break
                test_script += src[func_start:func_end] + "\n\n"

            if run_rollback:
                test_script += 'rollback_frontend "test-pod"\n'
            else:
                test_script += 'activate_frontend "test-pod"\n'

            script_path = Path(tmp) / "test.sh"
            script_path.write_text(test_script)
            script_path.chmod(0o755)

            result = subprocess.run(
                ["bash", str(script_path)],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"},
            )
            health_urls = health_log.read_text().strip()
            npm_env = npm_env_log.read_text().strip()
            return result, health_urls, npm_env

    def test_activate_health_check_uses_kaoyan_prefix(self) -> None:
        result, health_urls, _ = self._run_activate_or_rollback(
            context_path="/kaoyan"
        )
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        logged_urls = health_urls.splitlines()
        assert len(logged_urls) >= 1, f"No health URLs logged. stdout: {result.stdout}"
        for url in logged_urls:
            assert url == "http://127.0.0.1:3782/kaoyan/login", f"Wrong health URL: {url}"

    def test_activate_health_check_uses_root_without_prefix(self) -> None:
        result, health_urls, _ = self._run_activate_or_rollback(
            context_path=""
        )
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        logged_urls = health_urls.splitlines()
        assert len(logged_urls) >= 1
        for url in logged_urls:
            assert url == "http://127.0.0.1:3782/login", f"Wrong health URL: {url}"

    def test_rollback_health_check_uses_kaoyan_prefix(self) -> None:
        result, health_urls, _ = self._run_activate_or_rollback(
            context_path="/kaoyan", run_rollback=True
        )
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
        logged_urls = health_urls.splitlines()
        assert len(logged_urls) >= 1
        for url in logged_urls:
            assert url == "http://127.0.0.1:3782/kaoyan/login", f"Wrong health URL: {url}"

    def test_activate_health_prefix_matches_npm_build_prefix(self) -> None:
        """The health check URL and npm build env must use the same prefix."""
        # This test runs prepare + activate to capture both npm env and health URL
        with tempfile.TemporaryDirectory() as tmp:
            health_log = Path(tmp) / "health-urls.log"
            health_log.write_text("")
            npm_env_log = Path(tmp) / "npm-env.log"
            npm_env_log.write_text("")
            pod_manifest_path = Path(tmp) / "pod-routes-manifest.json"
            pod_manifest_path.write_text(json.dumps({"basePath": "/kaoyan"}))

            project = Path(tmp) / "project"
            web_next = project / "web" / ".next"
            web_next.mkdir(parents=True)
            standalone = web_next / "standalone" / ".next"
            standalone.mkdir(parents=True)
            (web_next / "standalone" / "server.js").write_text("")
            (web_next / "static").mkdir()
            (project / "web" / "public").mkdir()
            (web_next / "routes-manifest.json").write_text(
                json.dumps({"basePath": "/kaoyan"})
            )

            fake_kubectl = Path(tmp) / "kubectl"
            fake_kubectl.write_text(textwrap.dedent(f"""\
                #!/bin/bash
                all_args="$*"
                for arg in "$@"; do
                    if [[ "$arg" == *"import json"* ]]; then
                        modified=$(echo "$arg" | sed "s|/app/web/.next/routes-manifest.json|{pod_manifest_path}|g")
                        exec python3 -c "$modified"
                    fi
                done
                if [[ "$all_args" == *"pgrep"*"curl"* ]]; then
                    echo "${{@: -1}}" >> "{health_log}"
                    echo "12345 200"
                    exit 0
                fi
                if [[ "$all_args" == *"pgrep"*"kill"* ]]; then
                    echo "11111"
                    exit 0
                fi
                if [[ "$all_args" == *"hot-deploy"* ]]; then
                    exit 0
                fi
                exit 0
            """))
            fake_kubectl.chmod(0o755)

            fake_npm = Path(tmp) / "npm"
            fake_npm.write_text(textwrap.dedent(f"""\
                #!/bin/bash
                echo "NEXT_PUBLIC_CONTEXT_PATH=${{NEXT_PUBLIC_CONTEXT_PATH:-}}" > "{npm_env_log}"
                exit 0
            """))
            fake_npm.chmod(0o755)

            # Build script with prepare + activate
            src = SCRIPT
            test_script = textwrap.dedent(f"""\
                #!/bin/bash
                set -euo pipefail
                export PATH="{tmp}:$PATH"
                NAMESPACE="test"
                LABEL="app=test"
                CONTAINER="test"
                DEPLOY_ID="test-deploy-1"
                CONTEXT_PATH="/kaoyan"
                FRONTEND_PROCESS_PATTERN='^next-server'
                FRONTEND_HEALTH_URL="http://127.0.0.1:3782${{CONTEXT_PATH}}/login"
                HEALTH_ATTEMPTS=3
                HEALTH_STABLE_CHECKS=1
                PROJECT_ROOT="{project}"
                TMP_DIR="{tmp}"

                validate_frontend_runtime_inputs() {{ true; }}
            """)

            for func_name in [
                "wait_for_service",
                "restart_process",
                "prepare_frontend_archive",
                "rollback_frontend",
                "activate_frontend",
            ]:
                func_start = src.index(f"{func_name}()" + " {")
                brace_depth = 0
                func_end = func_start
                for i, ch in enumerate(src[func_start:], func_start):
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            func_end = i + 1
                            break
                test_script += src[func_start:func_end] + "\n\n"

            test_script += textwrap.dedent("""\
                prepare_frontend_archive "test-pod"
                activate_frontend "test-pod"
            """)

            script_path = Path(tmp) / "test.sh"
            script_path.write_text(test_script)
            script_path.chmod(0o755)

            result = subprocess.run(
                ["bash", str(script_path)],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"},
            )
            assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

            npm_env = npm_env_log.read_text().strip()
            assert npm_env == "NEXT_PUBLIC_CONTEXT_PATH=/kaoyan", f"npm got: {npm_env}"

            health_urls = health_log.read_text().strip().splitlines()
            assert len(health_urls) >= 1, f"No health URLs logged"
            for url in health_urls:
                assert url == "http://127.0.0.1:3782/kaoyan/login", f"Health URL: {url}"
