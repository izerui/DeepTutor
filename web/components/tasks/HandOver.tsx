"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass } from "@/components/common/form-styles";
import { listUsers, type UserRecord } from "@/lib/admin-api";

/** Active learners, for handing something over (spec 2026-11-05: any of them). */
export function activeLearners(users: UserRecord[]): UserRecord[] {
  return users
    .filter((user) => user.role !== "admin" && !user.disabled)
    .sort((a, b) => a.username.localeCompare(b.username));
}

/**
 * Traceability #5: what an administrator holds (e.g. from a departed author)
 * is handed to a learner, who becomes its author.
 */
export function HandOverPanel({
  handOver,
  onDone,
}: {
  handOver: (recipientId: string) => Promise<unknown>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [learners, setLearners] = useState<UserRecord[] | null>(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    listUsers()
      .then((users) => alive && setLearners(activeLearners(users)))
      .catch((err) => alive && (setLearners([]), setError(err instanceof Error ? t(err.message) : String(err))));
    return () => {
      alive = false;
    };
  }, [t]);

  if (learners === null) return null;
  const chosen = learners.find((learner) => learner.id === pick);
  return (
    <section className="space-y-2 rounded-lg border border-dashed border-[var(--border)] p-3" data-testid="hand-over">
      <h3 className="text-[12.5px] font-medium">{t("Hand over to a learner")}</h3>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("The learner you choose takes it over and becomes its author.")}
      </p>
      {learners.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No active learner to hand it to.")}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label={t("Learner")} className={`${inputClass} w-auto`} value={pick} disabled={busy}
            onChange={(e) => setPick(e.target.value)}>
            <option value="">{t("Choose a learner…")}</option>
            {learners.map((learner) => <option key={learner.id} value={learner.id}>{learner.username}</option>)}
          </select>
          <button type="button" className={actionClass} disabled={busy || !chosen}
            onClick={() => {
              if (!chosen || !window.confirm(t("Hand it over to {{name}}? You will no longer hold it.", { name: chosen.username }))) return;
              setBusy(true);
              setError("");
              handOver(chosen.id)
                .then(onDone)
                .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)))
                .finally(() => setBusy(false));
            }}>
            {t("Hand over")}
          </button>
        </div>
      )}
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
    </section>
  );
}
