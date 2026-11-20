"""The browser layout check against small hand-made pages (skipped without Chrome).

Upstream tests (d6fd29428, 3f027d943, ee678ec8c), adapted to the Popen runner,
plus the develop isolation rules (spec 2026-11-20 §4): sandboxed frame, CSP,
no network, process-group cleanup.
"""

from __future__ import annotations

import functools
import html
import http.server
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import pytest

from deeptutor.agents.video_lesson import layout_check
from deeptutor.agents.video_lesson.layout_check import (
    CAPTION_TOP,
    check_layout,
    chrome_path,
    sample_frames,
)



@functools.lru_cache(maxsize=1)
def _chrome_runs() -> bool:
    """A browser is installed *and* can start here (a sandboxed test run may forbid it)."""
    if chrome_path() is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp, "probe.html")
        page.write_text(
            '<!doctype html><html><body><div style="position:relative;width:1920px;'
            'height:1080px"></div></body></html>',
            encoding="utf-8",
        )
        return check_layout(page, [0], timeout_s=60) is not None


needs_chrome = pytest.mark.skipif(
    "not _chrome_runs()", reason="no Chrome/Chromium that can start in this environment"
)


def _page(tmp_path: Path, body: str) -> Path:
    page = tmp_path / "lesson.html"
    page.write_text(
        "<!doctype html><html><head><style>body{margin:0}div.t{position:absolute;font:48px sans-serif}"
        '</style></head><body><div style="position:relative;width:1920px;height:1080px">'
        + body
        + "</div></body></html>",
        encoding="utf-8",
    )
    return page


def test_sample_frames_spread_every_four_seconds() -> None:
    assert sample_frames(30 * 20) == [60, 180, 300, 420, 540]
    assert sample_frames(1) == [0]


@needs_chrome
def test_clean_page_has_no_issues(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        '<div class="t" style="left:200px;top:100px">Title</div>'
        '<div class="t" style="left:200px;top:400px">Body</div>',
    )
    assert check_layout(page, [0]) == []


@needs_chrome
def test_overlapping_text_is_reported(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        '<div class="t" style="left:200px;top:100px">Kernel namespaces</div>'
        '<div class="t" style="left:260px;top:110px">cgroups</div>',
    )
    issues = check_layout(page, [0])
    assert issues and any("overlaps text" in i for i in issues)


@needs_chrome
def test_overlapping_non_ascii_text_is_reported(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        '<div class="t" style="left:200px;top:100px">导数的定义</div>'
        '<div class="t" style="left:230px;top:110px">切线的斜率</div>',
    )
    issues = check_layout(page, [0])
    assert issues and any('"导数的定义" overlaps text "切线的斜率"' in i for i in issues)


def test_browser_output_is_decoded_as_utf8_whatever_the_locale(monkeypatch, tmp_path):
    # A Windows server whose ANSI code page is cp936 decodes ``text=True``
    # output with that code page unless an encoding is given; Chrome writes
    # --dump-dom as UTF-8, and the report quotes the lesson's text.
    issue = 'text "导数的定义" overlaps text "切线的斜率" near x=230, y=110'
    report = html.escape(json.dumps({"0": {"issues": [issue]}}, ensure_ascii=False))
    dom = f'<html><body><pre id="out">{report}</pre></body></html>\n'.encode()

    class Cp936ServerPopen:
        def __init__(self, argv, **kwargs):
            self.kwargs = kwargs
            self.pid = 0
            self.returncode = 0

        def communicate(self, timeout=None):
            encoding = self.kwargs.get("encoding") or "cp936"
            return dom.decode(encoding, self.kwargs.get("errors") or "strict"), ""

        def kill(self):
            pass

    browser = tmp_path / "chrome"
    browser.write_text("browser", encoding="utf-8")
    monkeypatch.setenv("DEEPTUTOR_CHROME_EXECUTABLE", str(browser))
    monkeypatch.setattr(layout_check.subprocess, "Popen", Cp936ServerPopen)
    monkeypatch.setattr(layout_check, "_kill_tree", lambda proc: None)

    assert check_layout(_page(tmp_path, ""), [0]) == [f"at 0s: {issue}"]


@needs_chrome
def test_stroke_through_text_and_caption_band_are_reported(tmp_path: Path) -> None:
    page = _page(
        tmp_path,
        '<div class="t" style="left:600px;top:300px">Image layer</div>'
        '<svg width="1920" height="1080" style="position:absolute;left:0;top:0">'
        '<path d="M 500 330 L 1100 330" stroke="black" stroke-width="4" fill="none"/>'
        f'<path d="M 100 700 L 100 {CAPTION_TOP + 120}" stroke="black" stroke-width="4" fill="none"/></svg>',
    )
    issues = check_layout(page, [0])
    assert issues is not None
    assert any('cross the text "Image layer"' in i for i in issues)
    assert any("caption band" in i for i in issues)


def test_browser_executable_can_be_configured(monkeypatch, tmp_path):
    browser = tmp_path / "custom-chrome"
    browser.write_text("browser", encoding="utf-8")
    monkeypatch.setenv("DEEPTUTOR_CHROME_EXECUTABLE", str(browser))
    assert chrome_path() == str(browser)
    monkeypatch.setenv("DEEPTUTOR_CHROME_EXECUTABLE", str(tmp_path / "missing"))
    assert chrome_path() is None


# ── isolation (develop) ──────────────────────────────────────────────


def test_probe_page_gets_a_no_network_csp_before_anything_else() -> None:
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<link href="https://fonts.googleapis.com/css2" rel="stylesheet"></head>'
        "<body><script>1</script></body></html>"
    )
    isolated = layout_check.isolate_page(page)
    head = isolated.index('<head>') + len("<head>")
    assert isolated[head:].startswith('<meta http-equiv="Content-Security-Policy"')
    assert isolated.index("Content-Security-Policy") < isolated.index("fonts.googleapis.com")
    assert "connect-src 'none'" in layout_check.PROBE_CSP
    assert "default-src 'none'" in layout_check.PROBE_CSP
    assert "http" not in layout_check.PROBE_CSP
    assert isolated.count("parent.postMessage") == 1


def test_lesson_frames_are_sandboxed_script_only() -> None:
    assert 'setAttribute("sandbox", "allow-scripts")' in layout_check._HOST
    assert "allow-same-origin" not in layout_check._HOST
    assert "default-src 'none'" in layout_check.HOST_CSP


def test_browser_has_no_network_and_no_file_access(tmp_path: Path) -> None:
    argv = layout_check.browser_argv("/bin/chrome", str(tmp_path), [0, 120])
    assert "--allow-file-access-from-files" not in argv
    assert f"--proxy-server={layout_check.UNREACHABLE_PROXY}" in argv
    assert "--proxy-bypass-list=<-loopback>" in argv
    assert "--host-resolver-rules=MAP * ~NOTFOUND" in argv
    assert "--disable-background-networking" in argv
    assert argv[-1] == (tmp_path / "host.html").as_uri()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
def test_a_hung_browser_and_its_children_are_killed(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    fake = tmp_path / "chrome.sh"
    fake.write_text(
        f"#!/bin/sh\nsleep 60 &\necho $! > {pid_file}\nsleep 60\n", encoding="utf-8"
    )
    fake.chmod(0o755)
    started = time.monotonic()
    assert layout_check._run_browser([str(fake)], timeout_s=1) is None
    assert time.monotonic() - started < 20
    child = int(pid_file.read_text().strip())
    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the browser's child process survived the timeout")


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):  # noqa: N802
        _Recorder.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    do_POST = do_GET
    do_CONNECT = do_GET

    def log_message(self, *args):
        pass


@pytest.fixture
def recorder():
    _Recorder.hits = []
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    except OSError as exc:  # a network-denied test sandbox
        pytest.skip(f"cannot listen on loopback here: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _exfiltrating_body(url: str) -> str:
    return (
        '<div class="t" style="left:200px;top:100px">Title</div>'
        f'<img src="{url}/img">'
        f'<link rel="stylesheet" href="{url}/css">'
        "<script>"
        f'fetch("{url}/fetch").catch(()=>{{}});'
        f'try{{navigator.sendBeacon("{url}/beacon","x")}}catch(e){{}}'
        f'try{{new WebSocket("{url.replace("http", "ws")}/ws")}}catch(e){{}}'
        f'try{{var x=new XMLHttpRequest();x.open("GET","{url}/xhr");x.send()}}catch(e){{}}'
        "</script>"
    )


@needs_chrome
def test_a_lesson_that_phones_home_reaches_nothing(tmp_path: Path, recorder: str) -> None:
    page = _page(tmp_path, _exfiltrating_body(recorder))
    issues = check_layout(page, [0])
    assert issues == []
    assert _Recorder.hits == []


@needs_chrome
def test_the_browser_flags_alone_block_the_network(tmp_path: Path, recorder: str) -> None:
    # Defence in depth: even without the CSP and the sandboxed frame, Chrome
    # started with the check's flags cannot reach a local server.
    (tmp_path / "host.html").write_text(
        "<!doctype html><html><body>" + _exfiltrating_body(recorder) + "</body></html>",
        encoding="utf-8",
    )
    argv = layout_check.browser_argv(chrome_path(), str(tmp_path), [0])
    proc = layout_check._run_browser(argv, 60)
    assert proc is not None
    assert _Recorder.hits == []

    # The same page with the flags removed does reach it (the test is real).
    plain = [a for a in argv if a not in layout_check.NO_NETWORK_FLAGS]
    layout_check._run_browser(plain, 60)
    assert _Recorder.hits
