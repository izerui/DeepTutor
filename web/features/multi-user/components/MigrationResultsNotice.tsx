"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Info } from "lucide-react";
import { fetchMigrationResults } from "@/features/multi-user/api";
import type { MigrationItem } from "@/features/multi-user/types";
import { useAuthStatus } from "@/hooks/useAuthStatus";

const list = (value: unknown): string =>
  Array.isArray(value) ? value.map(String).join(", ") : "";

/** Later migrations can add renderers here; unknown kinds retain their key. */
export const MIGRATION_ITEM_RENDERERS: Record<
  string,
  (item: MigrationItem, t: TFunction) => string
> = {
  kb_made_public: (item, t) =>
    t("Knowledge base “{{name}}” is now public because it was granted to: {{users}}", {
      name: String(item.data.name ?? item.key),
      users: list(item.data.users),
    }),
  connector_grant_disabled: (item, t) =>
    t(
      "Connector “{{name}}” stays private, so its grant was disabled. Affected accounts: {{users}}",
      { name: String(item.data.name ?? item.key), users: list(item.data.users) },
    ),
  partner_copy_adopted: (item, t) =>
    t(
      "Partner {{partner}} kept its own copy of “{{name}}” (the source no longer exists); it is now a private knowledge base of its creator.",
      {
        partner: String(item.data.partner_id ?? ""),
        name: String(item.data.name ?? item.key),
      },
    ),
  kb_dir_missing_skipped: (item, t) =>
    t(
      "Knowledge base “{{name}}” no longer exists, so it was not made public. It was granted to: {{users}}",
      { name: String(item.data.name ?? item.key), users: list(item.data.users) },
    ),
  book_auto_public: (item, t) =>
    t("Book “{{title}}” is now public because it was shared with: {{users}}", {
      title: String(item.data.title ?? item.key),
      users: list(item.data.users),
    }),
  book_edit_grant_retired: (item, t) =>
    t(
      "{{username}} could edit “{{title}}”. Editing is now limited to the creator and admins; consider transferring the book.",
      {
        username: String(item.data.username ?? ""),
        title: String(item.data.title ?? item.data.book_id ?? item.key),
      },
    ),
  book_left_shelf_skipped: (item, t) =>
    t(
      "Book “{{title}}” left the shared shelf before the upgrade, so it was not made public. It was shared with: {{users}}",
      { title: String(item.data.title ?? item.data.book_id ?? item.key), users: list(item.data.users) },
    ),
  book_kb_unavailable: (item, t) =>
    t("Public book “{{title}}” uses {{kb}}, which its readers cannot use.", {
      title: String(item.data.title ?? item.data.book_id ?? ""),
      kb: String(item.data.kb ?? ""),
    }),
};

export function MigrationResultsNotice() {
  const { t } = useTranslation();
  const auth = useAuthStatus();
  const canRead = !auth.loading && auth.statusAvailable && auth.isAdmin &&
    (!auth.enabled || auth.authenticated);
  const [items, setItems] = useState<MigrationItem[]>([]);

  useEffect(() => {
    if (!canRead) return;
    let alive = true;
    fetchMigrationResults()
      .then((next) => {
        if (alive) setItems(next);
      })
      .catch(() => {
        if (alive) setItems([]);
      });
    return () => {
      alive = false;
    };
  }, [canRead]);

  if (!canRead || items.length === 0) return null;
  return (
    <section className="mb-6 min-w-0 border-b border-[var(--border)] py-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold text-[var(--foreground)]">
        <Info size={14} aria-hidden="true" />
        {t("Migration results")}
      </h2>
      <p className="mt-1 text-xs text-[var(--muted-foreground)]">
        {t("These changes were made once when the sharing model was upgraded. Review them and adjust visibility where needed.")}
      </p>
      <ul className="mt-3 list-disc space-y-1 break-words pl-5 text-xs text-[var(--foreground)]">
        {items.map((item) => (
          <li key={`${item.kind}:${item.key}`}>
            {Object.hasOwn(MIGRATION_ITEM_RENDERERS, item.kind)
              ? MIGRATION_ITEM_RENDERERS[item.kind](item, t)
              : `${item.kind}: ${item.key}`}
          </li>
        ))}
      </ul>
    </section>
  );
}
