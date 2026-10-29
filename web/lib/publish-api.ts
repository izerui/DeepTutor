import { apiFetch, apiUrl } from "./api";

/** Publish scopes are system-level: never workspace-scoped (spec 2026-10-29 §7). */
const BASE = "/api/publish";

export type PublishKind = "book" | "knowledge_base" | "mastery_path";

export interface PublishScope {
  everyone: boolean;
  users: { id: string; username: string }[];
  classes: { id: string; name: string }[];
  /** False while the item still follows the old rule (public = everyone, paths = hand-picked students). */
  managed: boolean;
}

export interface ScopeDescription {
  kind: PublishKind;
  item_id: string;
  published: boolean;
  scope: PublishScope;
  audience_size: number;
  choices: { classes: { id: string; name: string }[] };
  suggested_classes: string[];
}

export interface ScopeUpdate {
  everyone: boolean;
  users: string[];
  classes: string[];
  confirm?: boolean;
}

export class PublishApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    /** Set when narrowing needs confirmation: how many people lose access. */
    public readonly affected: number | null = null,
  ) {
    super(message);
    this.name = "PublishApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`${BASE}${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const detail = typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`;
    throw new PublishApiError(detail, res.status, data?.needs_confirmation ? Number(data.affected) : null);
  }
  return (await res.json()) as T;
}

const itemPath = (kind: PublishKind, id: string) => `/${kind}/${encodeURIComponent(id)}/scope`;

export const getScope = (kind: PublishKind, id: string) => call<ScopeDescription>(itemPath(kind, id));

export const setScope = (kind: PublishKind, id: string, update: ScopeUpdate) =>
  call<ScopeDescription>(itemPath(kind, id), { method: "PUT", body: JSON.stringify(update) });

export const searchPeople = (query: string) =>
  call<{ people: { id: string; username: string; role: string }[] }>(`/people?q=${encodeURIComponent(query)}`).then(
    (r) => r.people,
  );

/**
 * Spec §7.1: a teacher publishes to the classes they teach unless they choose
 * otherwise. Applied right after an item first becomes public; nobody loses
 * access because nobody had it.
 */
export async function applyDefaultScope(kind: PublishKind, id: string): Promise<void> {
  const described = await getScope(kind, id);
  if (described.scope.managed || described.suggested_classes.length === 0) return;
  await setScope(kind, id, { everyone: false, users: [], classes: described.suggested_classes, confirm: true });
}
