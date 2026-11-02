import { apiFetch, apiUrl } from "./api";

export interface AppNotification {
  id: string;
  kind: string;
  params: Record<string, unknown>;
  link: string;
  count: number;
  created_at: number;
  read: boolean;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(apiUrl(`/api/notifications${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  return (await res.json()) as T;
}

export const listNotifications = () => call<{ notifications: AppNotification[]; unread: number }>("");
export const unreadNotifications = () => call<{ unread: number }>("/unread").then((r) => r.unread);
/** Without ``ids`` every notification is marked read. */
export const markNotificationsRead = (ids?: string[]) =>
  call<{ unread: number }>("/read", { method: "POST", body: JSON.stringify(ids ? { ids } : {}) }).then((r) => r.unread);
