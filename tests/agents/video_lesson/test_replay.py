"""Reopening a session replays the lesson (spec 2026-11-20 §5).

The settled message carries a bounded preview of the turn's events; the
``video_lesson`` result (the player page URL and the code) must survive it
even after a long turn full of streamed model output.
"""

from __future__ import annotations

from deeptutor.services.session.event_preview import compact_trace_preview


def test_the_lesson_result_survives_the_settled_preview() -> None:
    url = "/files/outputs/workspace/chat/video_lesson/turn-1/artifacts/lesson.html"
    events = [
        {"type": "thinking", "source": "video_lesson", "stage": "code_generation",
         "seq": seq, "content": "x" * 4000, "metadata": {"trace_kind": "llm_chunk"}}
        for seq in range(1, 300)
    ]
    events.append({
        "type": "result", "source": "video_lesson", "stage": "render_output", "seq": 400,
        "content": "", "metadata": {
            "title": "Calculus",
            "artifacts": [{"type": "html", "url": url, "filename": "lesson.html"}],
            "code": {"language": "tsx", "content": "const a = 1;\n" * 3000},
            "render": {"attempts": [{"attempt": 1, "stage": "done", "errors": []}],
                       "duration_seconds": 90, "narrated": True},
        },
    })
    events.append({"type": "done", "seq": 401, "content": "", "metadata": {"status": "completed"}})

    preview, truncated = compact_trace_preview(events)

    assert truncated
    result = next(event for event in preview if event["type"] == "result")
    assert result["metadata"]["artifacts"][0]["url"] == url
    assert result["metadata"]["code"]["content"].startswith("const a = 1;")
