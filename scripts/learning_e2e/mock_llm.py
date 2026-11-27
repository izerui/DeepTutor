"""Scripted OpenAI-compatible model for the learning-loop acceptance run.

Logs every request body to a JSONL file so the run can assert what the
backend actually sent the model. Triggers, matched in the learner's latest
message:

* ``QUOTE-CHECK`` — answers in one line.
* ``MQ-ASK``     — calls ``mastery_quiz`` once with a choice question on the
                   knowledge point written to ``<stage>/kp.txt``.
* ``NOTE-EDIT``  — reads the bound note and proposes an addition for review.
* anything else  — a fixed three-step explanation.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import time

PORT = int(sys.argv[1])
LOG = Path(sys.argv[2])
STAGE = Path(sys.argv[3])
ANSWER = (
    "第一步：对函数 f(x)=x^3 求导，得到 f'(x)=3x^2。\n\n"
    "第二步：令 f'(x)=0，解得驻点 x=0。\n\n"
    "第三步：因为 f'(x) 在 x=0 两侧都非负，所以 x=0 不是极值点。"
)


def _text(message):
    content = message.get("content")
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def plan(body):
    messages = body.get("messages") or []
    users = [m for m in messages if m.get("role") == "user"]
    if not users:
        return ANSWER, None
    last = users[-1]
    text = _text(last)
    if "QUOTE-CHECK" in text:
        return "收到引用：我会针对你引用的那一步解释。", None
    names = {(t.get("function") or t).get("name") for t in body.get("tools") or []}
    if "NOTE-EDIT" in text and "note_read" in names:
        index = max(i for i, m in enumerate(messages) if m is last)
        called = [
            call["function"]["name"]
            for m in messages[index + 1 :]
            for call in (m.get("tool_calls") or [])
        ]
        if "note_read" not in called:
            return None, ("note_read", {})
        if "note_suggest" not in called:
            return None, ("note_suggest", {
                "operation": "insert", "block_id": "",
                "markdown": "An assistant addition, accepted by the learner.",
                "explanation": "A proposed addition for review.",
            })
        return "Review the proposed addition.", None
    retest = "MQ-RETEST" in text
    if ("MQ-ASK" in text or retest) and "mastery_quiz" in names:
        index = max(i for i, m in enumerate(messages) if m is last)
        called = [
            call["function"]["name"]
            for m in messages[index + 1 :]
            for call in (m.get("tool_calls") or [])
        ]
        if "mastery_quiz" not in called:
            kp = (STAGE / "kp.txt").read_text().strip()
            return None, (
                "mastery_quiz",
                {
                    "knowledge_point_id": kp,
                    "question": "x 的五次方在 x=1 处的导数是多少？" if retest else "x 的四次方在 x=1 处的导数是多少？",
                    "question_type": "choice",
                    "options": [{"label": "A", "body": "3"}, {"label": "B", "body": "5" if retest else "4"}],
                    "expected_answer": "B",
                },
            )
        return "好的，我们看看你的答案。", None
    return ANSWER, None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self._json({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})

    def _json(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode())

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(size) or b"{}")
        with LOG.open("a") as handle:
            handle.write(json.dumps({"path": self.path, "body": body}, ensure_ascii=False) + "\n")
        text, tool = plan(body)
        base = {"id": "chatcmpl-mock", "created": int(time.time()), "model": "mock-model"}
        if not body.get("stream"):
            self._json({**base, "object": "chat.completion", "choices": [
                {"index": 0, "message": {"role": "assistant", "content": text or ""}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(delta, finish=None):
            chunk = {**base, "object": "chat.completion.chunk",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
            self.wfile.flush()

        event({"role": "assistant", "content": ""})
        if tool:
            name, args = tool
            event({"tool_calls": [{"index": 0, "id": f"call_{name}_{time.time_ns()}", "type": "function",
                                   "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]})
            event({}, "tool_calls")
        else:
            for start in range(0, len(text), 12):
                event({"content": text[start : start + 12]})
            event({}, "stop")
        usage = {**base, "object": "chat.completion.chunk", "choices": [],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        self.wfile.write(("data: " + json.dumps(usage) + "\n\ndata: [DONE]\n\n").encode())
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
