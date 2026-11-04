"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass } from "@/components/common/form-styles";
import { listUsers, type UserRecord } from "@/lib/admin-api";

/** Active teachers, for handing something over. */
export function activeTeachers(users: UserRecord[]): UserRecord[] {
  return users
    .filter((user) => user.role === "teacher" && !user.disabled)
    .sort((a, b) => a.username.localeCompare(b.username));
}

/**
 * Traceability #5: an administrator may not grade homework, so what they hold
 * (e.g. from a departed teacher) is handed to a teacher who grades it.
 */
export function HandOverPanel({
  handOver,
  onDone,
}: {
  handOver: (teacherId: string) => Promise<unknown>;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [teachers, setTeachers] = useState<UserRecord[] | null>(null);
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    listUsers()
      .then((users) => alive && setTeachers(activeTeachers(users)))
      .catch((err) => alive && (setTeachers([]), setError(err instanceof Error ? t(err.message) : String(err))));
    return () => {
      alive = false;
    };
  }, [t]);

  if (teachers === null) return null;
  const chosen = teachers.find((teacher) => teacher.id === pick);
  return (
    <section className="space-y-2 rounded-lg border border-dashed border-[var(--border)] p-3" data-testid="hand-over">
      <h3 className="text-[12.5px] font-medium">{t("Hand over to a teacher")}</h3>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">
        {t("Administrators do not grade homework. The teacher you choose takes it over, with its homework, and grades it.")}
      </p>
      {teachers.length === 0 ? (
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("No active teacher to hand it to.")}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label={t("Teacher")} className={`${inputClass} w-auto`} value={pick} disabled={busy}
            onChange={(e) => setPick(e.target.value)}>
            <option value="">{t("Choose a teacher…")}</option>
            {teachers.map((teacher) => <option key={teacher.id} value={teacher.id}>{teacher.username}</option>)}
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
