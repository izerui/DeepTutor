import { isQuoteAttachment, quoteText } from "./quote-attachments";

/**
 * Minimal message/attachment shape the markdown exporter needs. Product chat's
 * `MessageItem` satisfies it structurally, and partner conversations map into
 * it too — so both surfaces share one serializer.
 */
export interface ExportableAttachment {
  type?: string;
  filename?: string;
  mime_type?: string;
  /** Quote attachments carry their passage in one of these. */
  text?: string;
  extracted_text?: string;
  base64?: string;
}

export interface ExportableMessage {
  role: string;
  content: string;
  /** Optional human-readable author for multi-speaker conversations. */
  speaker?: string;
  capability?: string;
  attachments?: ExportableAttachment[];
}

function roleHeading(role: string): string {
  if (role === "user") return "User";
  if (role === "assistant") return "Assistant";
  return "System";
}

function messageHeading(message: ExportableMessage): string {
  // Keep an untrusted display name on one Markdown heading line. Markdown
  // punctuation is harmless here, but line breaks could forge extra blocks.
  const speaker = message.speaker?.trim().replace(/\s+/g, " ");
  return speaker || roleHeading(message.role);
}

function formatAttachments(attachments?: ExportableAttachment[]): string {
  const files = attachments?.filter((a) => !isQuoteAttachment(a)) ?? [];
  if (!files.length) return "";
  const items = files
    .map((a) => {
      const name = a.filename || a.type || "attachment";
      return a.mime_type ? `\`${name}\` (${a.mime_type})` : `\`${name}\``;
    })
    .join(", ");
  return `_Attachments:_ ${items}\n\n`;
}

/** Quoted passages, as Markdown blockquotes in front of the question. */
function formatQuotes(attachments?: ExportableAttachment[]): string {
  return (attachments ?? [])
    .filter(isQuoteAttachment)
    .map((a) => quoteText(a).trim())
    .filter(Boolean)
    .map((text) => text.split("\n").map((line) => `> ${line}`.trimEnd()).join("\n") + "\n\n")
    .join("");
}

export interface BuildChatMarkdownOptions {
  title?: string;
  exportedAt?: Date;
}

export function buildChatMarkdown(
  messages: ExportableMessage[],
  options: BuildChatMarkdownOptions = {},
): string {
  const title = options.title?.trim() || "Chat Session";
  const exportedAt = (options.exportedAt ?? new Date()).toISOString();
  const header = `# ${title}\n\n_Exported: ${exportedAt}_\n\n---\n\n`;
  const body = messages
    .map((msg) => {
      const role = messageHeading(msg);
      const cap = msg.capability ? ` _(${msg.capability})_` : "";
      const attachments = formatAttachments(msg.attachments);
      const quotes = formatQuotes(msg.attachments);
      const content = (msg.content ?? "").trim();
      return `## ${role}${cap}\n\n${attachments}${quotes}${content}`.trimEnd();
    })
    .join("\n\n---\n\n");
  return header + body + "\n";
}

function sanitizeFilename(input: string): string {
  const cleaned = input
    .replace(/[\\/:*?"<>|\n\r\t]/g, "")
    .replace(/\s+/g, "-")
    .slice(0, 60);
  return cleaned || "chat";
}

export function downloadChatMarkdown(
  messages: ExportableMessage[],
  options: BuildChatMarkdownOptions = {},
): void {
  if (!messages.length) return;
  const markdown = buildChatMarkdown(messages, options);
  const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  const date = new Date().toISOString().slice(0, 10);
  anchor.download = `${sanitizeFilename(options.title || "chat")}-${date}.md`;
  anchor.click();
  URL.revokeObjectURL(url);
}
