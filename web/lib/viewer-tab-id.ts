import type { MessageAttachment } from "@/features/chat/ChatStateAdapter";

export function fileTabIdFor(a: MessageAttachment, fallback: number): string {
  return `file:${a.workspace_item_id ?? a.id ?? a.url ?? a.filename ?? `idx-${fallback}`}`;
}
