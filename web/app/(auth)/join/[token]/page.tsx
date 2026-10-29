"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useTranslation } from "react-i18next";

import { fetchAuthStatus } from "@/lib/auth";
import { inviteLinkInfo, joinClass } from "@/lib/classes-api";

type State =
  | { kind: "loading" }
  | { kind: "invalid"; message: string }
  | { kind: "ready"; className: string; signedIn: boolean; isStudent: boolean };

/**
 * A class invite link (spec 2026-10-29 §5). Signed out: name the class and
 * offer sign-in or registration, returning here. Signed in as a student: join.
 */
export default function JoinClassPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const params = useParams<{ token: string }>();
  const token = String(params?.token ?? "");
  const [state, setState] = useState<State>({ kind: "loading" });
  const [error, setError] = useState("");
  const [joining, setJoining] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([inviteLinkInfo(token), fetchAuthStatus()])
      .then(([info, status]) => {
        if (!alive) return;
        setState({
          kind: "ready",
          className: info.class_name,
          signedIn: Boolean(status?.authenticated),
          isStudent: status?.role === "student",
        });
      })
      .catch((err) => {
        if (alive) setState({ kind: "invalid", message: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
  }, [token]);

  const here = `/join/${encodeURIComponent(token)}`;

  async function join() {
    setJoining(true);
    setError("");
    try {
      await joinClass(token);
      router.replace("/classes");
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
      setJoining(false);
    }
  }

  return (
    <div className="w-full max-w-sm space-y-5 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-7 text-center">
      {state.kind === "loading" && <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />}
      {state.kind === "invalid" && (
        <>
          <h1 className="text-[18px] font-semibold text-[var(--foreground)]">{t("This invite link cannot be used")}</h1>
          <p className="text-[13px] text-[var(--muted-foreground)]">{t(state.message)}</p>
          <Link href="/" className="text-[13px] text-[var(--primary)] underline">{t("Go to home")}</Link>
        </>
      )}
      {state.kind === "ready" && (
        <>
          <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("You are invited to join")}</p>
          <h1 className="text-[22px] font-semibold text-[var(--foreground)]">{state.className}</h1>
          {!state.signedIn ? (
            <div className="space-y-2">
              <Link
                href={`/login?next=${encodeURIComponent(here)}`}
                className="block rounded-lg bg-[var(--primary)] px-4 py-2.5 text-[13.5px] font-semibold text-[var(--primary-foreground)]"
              >
                {t("Sign in to join")}
              </Link>
              <Link
                href={`/register?next=${encodeURIComponent(here)}&invite=${encodeURIComponent(token)}`}
                className="block rounded-lg border border-[var(--border)] px-4 py-2.5 text-[13.5px] font-medium text-[var(--foreground)]"
              >
                {t("Create an account")}
              </Link>
            </div>
          ) : state.isStudent ? (
            <button
              type="button"
              disabled={joining}
              onClick={() => void join()}
              className="w-full rounded-lg bg-[var(--primary)] px-4 py-2.5 text-[13.5px] font-semibold text-[var(--primary-foreground)] disabled:opacity-50"
            >
              {t("Join class")}
            </button>
          ) : (
            <p className="text-[13px] text-[var(--muted-foreground)]">
              {t("Only student accounts join a class with an invite link.")}
            </p>
          )}
          {error && <p role="alert" className="text-[12.5px] text-[var(--destructive)]">{error}</p>}
        </>
      )}
    </div>
  );
}
