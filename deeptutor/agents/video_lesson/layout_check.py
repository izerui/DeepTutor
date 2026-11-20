"""Browser layout check for a built video lesson.

The compile/render check proves the lesson runs; it cannot see that a drawing
crosses a title or that a label lands on a card. This opens the finished page
in headless Chrome at sample frames, measures every visible text box and
samples points along every visible SVG stroke, and reports collisions in the
1920x1080 composition's own coordinates so the writer can move things.

Ported from upstream (d6fd29428, 3f027d943, ee678ec8c) with the develop rules
(spec 2026-11-20 §4): the check opens model-written script in the server's
browser, so it is OFF unless an administrator turns it on, and when it runs
the page is isolated three ways:

* the lesson loads in an ``<iframe sandbox="allow-scripts">`` (opaque origin,
  no same-origin access to the host page, the file system or storage);
* both pages carry a Content-Security-Policy that allows inline script and
  ``data:``/``blob:`` media only, so nothing can be fetched or sent;
* Chrome itself has no network: every request goes to an unreachable proxy
  (loopback included), host names never resolve, and background services are
  off; no ``--allow-file-access-from-files``.

A missing browser, a timeout or any probe failure means "could not check"
(``None``) and the pipeline keeps the lesson that already builds.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess  # nosec B404 - fixed argv, local file only
import tempfile

CAPTION_TOP = 880  # the kit's captions own everything below this line
_MAX_ISSUES = 14

# Inline script and in-page media only: no fetch, no XHR, no websockets, no
# remote fonts/images, no forms, no navigation of the top frame.
PROBE_CSP = "; ".join(
    (
        "default-src 'none'",
        "script-src 'unsafe-inline' blob:",
        "style-src 'unsafe-inline'",
        "img-src data: blob:",
        "media-src data: blob:",
        "font-src data:",
        "connect-src 'none'",
        "worker-src blob:",
        "base-uri 'none'",
        "form-action 'none'",
    )
)
HOST_CSP = "default-src 'none'; script-src 'unsafe-inline'; frame-src file:; style-src 'unsafe-inline'"

# Requests that somehow escape the CSP land on a closed loopback port; the
# bypass list removes the implicit loopback exemption, and host names never
# resolve. Chrome's own background traffic is switched off.
UNREACHABLE_PROXY = "http://127.0.0.1:9"
NO_NETWORK_FLAGS = (
    f"--proxy-server={UNREACHABLE_PROXY}",
    "--proxy-bypass-list=<-loopback>",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-sync",
    "--no-default-browser-check",
    "--no-pings",
    "--mute-audio",
)

_PROBE = r"""
<script>
(function () {
  function opacityOf(el) {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === "none" || cs.visibility === "hidden") return 0;
      o *= parseFloat(cs.opacity || "1");
    }
    return o;
  }
  function probe() {
    const root = [...document.querySelectorAll("div")].find(
      (d) => d.style.width === "1920px" && d.style.height === "1080px");
    if (!root) return {error: "composition root not found"};
    const r = root.getBoundingClientRect(), k = r.width / 1920;
    const toC = (x, y) => [(x - r.left) / k, (y - r.top) / k];
    const texts = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const s = n.textContent.trim();
      if (!s || !n.parentElement || opacityOf(n.parentElement) < 0.15) continue;
      const range = document.createRange(); range.selectNodeContents(n);
      for (const b of range.getClientRects()) {
        if (b.width < 2 || b.height < 2) continue;
        const [x0, y0] = toC(b.left, b.top), [x1, y1] = toC(b.right, b.bottom);
        const cap = !!n.parentElement.closest('div[style*="bottom: 64px"]');
        texts.push({el: n.parentElement, s: s.slice(0, 48), cap, x0, y0, x1, y1});
      }
    }
    const issues = [];
    // text on text
    for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
      const a = texts[i], b = texts[j];
      if (a.cap || b.cap || a.el === b.el || a.el.contains(b.el) || b.el.contains(a.el)) continue;
      const w = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0), h = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
      if (w > 4 && h > 4) issues.push(`text "${a.s}" overlaps text "${b.s}" near x=${Math.round(Math.max(a.x0, b.x0))}, y=${Math.round(Math.max(a.y0, b.y0))}`);
    }
    // text leaving the safe area
    for (const t of texts) {
      if (t.cap) {
        if (t.y0 < __CAPTION_TOP__ - 60) issues.push(`the caption "${t.s}" wraps to three lines and climbs to y=${Math.round(t.y0)}; split that narration sentence (keep each under 18 words)`);
        continue;
      }
      if (t.y1 > __CAPTION_TOP__ + 2) issues.push(`text "${t.s}" reaches y=${Math.round(t.y1)}, inside the caption band (keep everything above y=${__CAPTION_TOP__})`);
      if (t.x0 < 40 || t.x1 > 1880 || t.y0 < 20) issues.push(`text "${t.s}" runs off the frame edge`);
    }
    // strokes through text / into the caption band
    // opaque boxes (cards, panels) covering text that is not inside them
    const opaque = (cs) => {
      const m = (cs.backgroundColor || "").match(/rgba?\(([^)]+)\)/);
      if (!m) return false;
      const parts = m[1].split(",").map(Number);
      return parts.length < 4 || parts[3] > 0.6;
    };
    for (const box of root.querySelectorAll("div,section,aside")) {
      if (box === root || opacityOf(box) < 0.15 || !opaque(getComputedStyle(box))) continue;
      const b = box.getBoundingClientRect();
      const [bx0, by0] = toC(b.left, b.top), [bx1, by1] = toC(b.right, b.bottom);
      if (bx1 - bx0 < 40 || by1 - by0 < 20 || (bx1 - bx0 > 1800 && by1 - by0 > 1000)) continue;
      const label = (box.textContent || "").trim().slice(0, 32);
      for (const t of texts) {
        if (t.cap || box.contains(t.el) || t.el.contains(box)) continue;
        const w = Math.min(t.x1, bx1) - Math.max(t.x0, bx0), h = Math.min(t.y1, by1) - Math.max(t.y0, by0);
        if (w > 4 && h > 4) issues.push(`the box "${label}" (x ${Math.round(bx0)}-${Math.round(bx1)}, y ${Math.round(by0)}-${Math.round(by1)}) covers the text "${t.s}"; move one of them`);
      }
    }
    const crossed = new Map(); const bandHits = [];
    const shapes = root.querySelectorAll("path,line,polyline,polygon,circle,ellipse,rect");
    for (const g of shapes) {
      if (!g.getTotalLength || opacityOf(g) < 0.15) continue;
      const cs = getComputedStyle(g);
      if (cs.stroke === "none" || parseFloat(cs.strokeWidth || "0") <= 0) continue;
      let L; try { L = g.getTotalLength(); } catch (e) { continue; }
      if (!L || L < 4) continue;
      let visible = L;
      const dash = (cs.strokeDasharray || "none").split(/[ ,]+/).map(parseFloat).filter((v) => !isNaN(v));
      if (dash.length && dash[0] >= L * 0.95) visible = Math.max(0, L - Math.abs(parseFloat(cs.strokeDashoffset || "0")));
      const m = g.getScreenCTM(); if (!m) continue;
      const svg = g.ownerSVGElement;
      const hitText = new Map(); let inBand = null;
      const n = Math.min(400, Math.max(8, Math.ceil(visible / 6)));
      for (let i = 0; i <= n; i++) {
        const p = g.getPointAtLength((visible * i) / n);
        const sp = svg.createSVGPoint(); sp.x = p.x; sp.y = p.y;
        const q = sp.matrixTransform(m); const [x, y] = toC(q.x, q.y);
        if (y > __CAPTION_TOP__ + 3 && y < 1080 && x > 0 && x < 1920) inBand = inBand || [x, y];
        for (const t of texts) {
          if (x > t.x0 + 2 && x < t.x1 - 2 && y > t.y0 + 2 && y < t.y1 - 2) {
            const e = hitText.get(t.s) || {count: 0, x, y}; e.count++; hitText.set(t.s, e);
          }
        }
      }
      for (const [s, e] of hitText) if (e.count >= 2) {
        const c = crossed.get(s) || {n: 0, x: e.x, y: e.y}; c.n++; crossed.set(s, c);
      }
      if (inBand) bandHits.push(inBand);
    }
    for (const [s, c] of crossed) issues.push(`${c.n} drawn line${c.n > 1 ? "s" : ""} cross the text "${s}" near x=${Math.round(c.x)}, y=${Math.round(c.y)}; move the text or shrink/move the drawing so they no longer meet`);
    if (bandHits.length) {
      const ys = bandHits.map((p) => p[1]), xs = bandHits.map((p) => p[0]);
      issues.push(`${bandHits.length} drawn shape${bandHits.length > 1 ? "s" : ""} enter the caption band (x ${Math.round(Math.min(...xs))}-${Math.round(Math.max(...xs))}, down to y=${Math.round(Math.max(...ys))}); keep every drawing above y=${__CAPTION_TOP__}`);
    }
    return {issues: [...new Set(issues)]};
  }
  window.addEventListener("load", () => setTimeout(() => {
    let out; try { out = probe(); } catch (e) { out = {error: String(e)}; }
    parent.postMessage({frame: new URLSearchParams(location.search).get("frame"), ...out}, "*");
  }, 1500));
})();
</script>
"""

_HOST = """<!doctype html><html><head><meta http-equiv="Content-Security-Policy" content="__HOST_CSP__">
</head><body><pre id="out"></pre><script>
const frames = __FRAMES__, got = {};
for (const f of frames) {
  const i = document.createElement("iframe");
  i.setAttribute("sandbox", "allow-scripts");
  i.width = 1280; i.height = 760; i.src = "probe.html?frame=" + f;
  document.body.appendChild(i);
}
addEventListener("message", (e) => {
  if (!e.data || typeof e.data !== "object" || !frames.map(String).includes(String(e.data.frame))) return;
  got[e.data.frame] = e.data;
  if (Object.keys(got).length === frames.length) document.getElementById("out").textContent = JSON.stringify(got);
});
</script></body></html>"""


def chrome_path() -> str | None:
    """Use an explicit browser first, then standard cross-platform installs."""
    configured = os.environ.get("DEEPTUTOR_CHROME_EXECUTABLE", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.is_file() else None
    for name in (
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "chrome",
        "msedge",
    ):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
    ]
    for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(name)
        if root:
            candidates.extend(
                Path(root) / suffix
                for suffix in (
                    "Google/Chrome/Application/chrome.exe",
                    "Microsoft/Edge/Application/msedge.exe",
                )
            )
    return next((str(path) for path in candidates if path.is_file()), None)


def sample_frames(total: int, fps: int = 30, every_s: float = 4.0) -> list[int]:
    step = max(1, int(every_s * fps))
    return list(range(step // 2, max(total - 1, 1), step)) or [0]


def isolate_page(html: str) -> str:
    """The lesson page with the probe and a no-network CSP as its first head element."""
    meta = f'<meta http-equiv="Content-Security-Policy" content="{PROBE_CSP}">'
    probe = _PROBE.replace("__CAPTION_TOP__", str(CAPTION_TOP))
    match = re.search(r"<head[^>]*>", html, re.I)
    if match:
        html = html[: match.end()] + meta + html[match.end() :]
    else:
        html = meta + html
    if "</body>" in html:
        return html.replace("</body>", probe + "</body>", 1)
    return html + probe


def browser_argv(chrome: str, tmp: str, frames: list[int]) -> list[str]:
    return [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        # Virtual time (below) only drives the host page's renderer; a sandboxed
        # frame moved into its own process would never reach its probe timer.
        # It still runs in an opaque origin under the renderer's OS sandbox, next
        # to a host page that holds nothing but this file's own script.
        "--disable-features=IsolateSandboxedIframes",
        *NO_NETWORK_FLAGS,
        f"--user-data-dir={tmp}/profile",
        f"--virtual-time-budget={8000 + 400 * len(frames)}",
        "--dump-dom",
        Path(tmp, "host.html").as_uri(),
    ]


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_browser(argv: list[str], timeout_s: int) -> subprocess.CompletedProcess | None:
    """Run Chrome in its own process group; kill the whole group on timeout."""
    kwargs: dict = {"start_new_session": True} if os.name == "posix" else {}
    try:
        # --dump-dom is UTF-8 and quotes the lesson's own text; the server's
        # ANSI code page (cp936, cp932, ...) would mangle it into bad JSON.
        proc = subprocess.Popen(  # nosec B603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            **kwargs,
        )
    except OSError:
        return None
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        return None
    except BaseException:
        _kill_tree(proc)
        raise
    # Helpers (renderer, GPU, network service) must not outlive the browser.
    _kill_tree(proc)
    return subprocess.CompletedProcess(argv, proc.returncode, stdout=stdout, stderr=stderr)


def check_layout(page: Path, frames: list[int], timeout_s: int = 180) -> list[str] | None:
    """Collision report for ``page`` at ``frames``; ``None`` when no browser is available."""
    chrome = chrome_path()
    if not chrome or not frames:
        return None
    with tempfile.TemporaryDirectory(prefix="vl-layout-") as tmp:
        html = page.read_text(encoding="utf-8")
        Path(tmp, "probe.html").write_text(isolate_page(html), encoding="utf-8")
        Path(tmp, "host.html").write_text(
            _HOST.replace("__FRAMES__", json.dumps(frames)).replace("__HOST_CSP__", HOST_CSP),
            encoding="utf-8",
        )
        proc = _run_browser(browser_argv(chrome, tmp, frames), timeout_s)
    if proc is None or proc.returncode != 0:
        return None
    m = re.search(r'<pre id="out">(.*?)</pre>', proc.stdout, re.S)
    if not m or not m.group(1).strip():
        return None
    try:
        data = json.loads(
            m.group(1)
            .replace("&quot;", '"')
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&amp;", "&")
        )
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or any(
        not isinstance(data.get(str(frame)), dict) or data[str(frame)].get("error")
        for frame in frames
    ):
        return None
    seen: dict[str, list[int]] = {}
    for frame in sorted(data, key=lambda f: int(f)):
        for issue in data[frame].get("issues") or []:
            seen.setdefault(issue, []).append(int(frame))
    issues = [
        f"at {', '.join(f'{f / 30:.0f}s' for f in frames[:4])}: {issue}"
        for issue, frames in seen.items()
    ]
    return issues[:_MAX_ISSUES]
