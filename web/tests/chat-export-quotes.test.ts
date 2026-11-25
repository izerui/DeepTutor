import test from "node:test";
import assert from "node:assert/strict";

import { buildChatMarkdown } from "../lib/chat-export";

const exportedAt = new Date("2026-11-25T00:00:00Z");

test("chat export writes a saved quoted passage before the question", () => {
  const markdown = buildChatMarkdown(
    [
      {
        role: "user",
        content: "为什么这里要令导数为 0？",
        attachments: [
          {
            type: "quote",
            filename: "quote.md",
            mime_type: "text/markdown",
            extracted_text: "第二步：令 f'(x)=0，\n解得驻点 x=0。",
            base64: "",
          },
        ],
      },
    ],
    { exportedAt },
  );

  assert.match(
    markdown,
    /## User\n\n> 第二步：令 f'\(x\)=0，\n> 解得驻点 x=0。\n\n为什么这里要令导数为 0？/,
  );
  assert.doesNotMatch(markdown, /quote\.md/);
});

test("chat export decodes a composer quote and still lists real files", () => {
  const markdown = buildChatMarkdown(
    [
      {
        role: "user",
        content: "Explain this",
        attachments: [
          {
            type: "quote",
            filename: "quote.md",
            base64: Buffer.from("Step two", "utf8").toString("base64"),
          },
          { type: "file", filename: "notes.pdf", mime_type: "application/pdf" },
        ],
      },
    ],
    { exportedAt },
  );

  assert.match(
    markdown,
    /_Attachments:_ `notes\.pdf` \(application\/pdf\)\n\n> Step two\n\nExplain this/,
  );
});
