"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, RefreshCw, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
} from "@/components/settings/shared";
import { useSettings } from "@/features/settings/store/SettingsStore";
import { useStagedSettings } from "@/features/settings/store/useStagedSettings";
import {
  getVideoLessonSettings,
  getVideoLessonStatus,
  installVideoLessonRuntime,
  type VideoLessonAdminSettings,
  type VideoLessonStatus,
} from "@/lib/video-lesson-admin-api";

/**
 * Video Lesson administration (spec 2026-11-20 §2–§4): who may use it, the
 * per-lesson budget, the optional browser layout check, and the server's
 * Node.js runtime with its npm mirror. Admin-only page; the endpoints refuse
 * everyone else.
 */
const DEFAULT_NPM_REGISTRY = "https://registry.npmjs.org";

const DEFAULTS: VideoLessonAdminSettings = {
  learners_allowed: true,
  temperature: 0.5,
  max_tokens: 24000,
  max_attempts: 3,
  layout_check: false,
  npm_registry: "",
};

function runtimeReason(
  status: VideoLessonStatus,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const { runtime } = status;
  if (runtime.reason === "node_missing")
    return t("Node.js is not installed on the server.");
  if (runtime.reason === "node_too_old")
    return t("Node.js {{version}} is too old; version {{major}} or newer is required.", {
      version: runtime.node_version ?? "?",
      major: status.min_node_major,
    });
  if (runtime.reason === "npm_missing")
    return t("npm is not installed on the server.");
  return runtime.installed
    ? t("Ready. The renderer packages are installed.")
    : t("Node.js is ready. The renderer packages are installed on first use.");
}

export default function VideoLessonSettingsPage() {
  const { t } = useTranslation();
  const { draftRevision } = useSettings();
  const [live, setLive] = useState(DEFAULTS);
  const [settings, setSettings] = useStagedSettings("video-lesson", live, setLive);
  const [status, setStatus] = useState<VideoLessonStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installMessage, setInstallMessage] = useState("");

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await getVideoLessonStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getVideoLessonSettings(), getVideoLessonStatus()])
      .then(([loaded, loadedStatus]) => {
        if (cancelled) return;
        setLive(loaded);
        setStatus(loadedStatus);
        setError("");
      })
      .catch((err) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : t("Settings could not be loaded."),
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [t, draftRevision]);

  const install = async () => {
    setInstalling(true);
    setInstallMessage(t("Installing the renderer packages…"));
    try {
      setStatus(await installVideoLessonRuntime());
      setInstallMessage(t("The renderer packages are installed."));
    } catch (err) {
      setInstallMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  };

  const patch = (value: Partial<VideoLessonAdminSettings>) =>
    setSettings((current) => ({ ...current, ...value }));

  if (loading) {
    return (
      <div className="grid h-[60vh] place-items-center text-[13px] text-[var(--muted-foreground)]">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }

  return (
    <div>
      <SettingsPageHeader
        title={t("Video Lesson")}
        description={t(
          "Narrated explainer animations generated in chat. Each lesson can use a lot of AI usage, so you decide who may use it and how much one lesson may spend.",
        )}
      />
      {error ? (
        <p className="mb-6 text-[13px] text-[var(--destructive,#b91c1c)]">{error}</p>
      ) : null}

      <SettingSection
        title={t("Access and budget")}
        description={t(
          "Every model call counts toward the learner's AI daily usage.",
        )}
      >
        <ToggleRow
          label={t("Learners can use Video Lesson")}
          help={t(
            "When off, only administrators see Video Lesson; learners who try it get a clear message.",
          )}
          value={settings.learners_allowed}
          onChange={(value) => patch({ learners_allowed: value })}
        />
        <NumberRow
          label={t("Max tokens per writing attempt")}
          value={settings.max_tokens}
          onChange={(value) => patch({ max_tokens: value })}
          min={256}
          max={200000}
          step={1000}
        />
        <NumberRow
          label={t("Max attempts")}
          help={t(
            "How many times the lesson may be written or repaired before the turn gives up.",
          )}
          value={settings.max_attempts}
          onChange={(value) => patch({ max_attempts: value })}
          min={1}
          max={status?.max_attempts_limit ?? 6}
        />
        <NumberRow
          label={t("Temperature")}
          value={settings.temperature}
          onChange={(value) => patch({ temperature: value })}
          min={0}
          max={2}
          step={0.05}
          isFloat
        />
      </SettingSection>

      <SettingSection
        title={t("Layout check")}
        description={t(
          "Opens each generated lesson in the server's headless Chrome to find overlapping text and asks the model to fix it. The page runs isolated with no network access. Off by default.",
        )}
      >
        <ToggleRow
          label={t("Check layout in a headless browser")}
          value={settings.layout_check}
          onChange={(value) => patch({ layout_check: value })}
        />
        <SettingRow
          title={t("Browser")}
          description={
            status?.browser.available
              ? (status.browser.path ?? "")
              : t("No Chrome or Chromium found; the check is skipped.")
          }
          control={<StatusIcon ok={Boolean(status?.browser.available)} />}
        />
      </SettingSection>

      <SettingSection
        title={t("Runtime")}
        description={t(
          "Video Lesson needs Node.js and npm on the server. The renderer packages are installed once for the whole site.",
        )}
      >
        <SettingRow
          title={t("Node.js")}
          description={status ? runtimeReason(status, t) : ""}
          control={
            <div className="flex items-center gap-2">
              <StatusIcon ok={Boolean(status?.runtime.available)} />
              <button
                type="button"
                onClick={() => void refreshStatus()}
                aria-label={t("Refresh status")}
                className="rounded-md border border-[var(--border)] p-1 hover:bg-[var(--muted)]"
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
            </div>
          }
        />
        {status?.runtime.node_version ? (
          <SettingRow
            title={t("Version")}
            description={`${status.runtime.node_version} · ${status.runtime.runtime_dir}`}
            control={null}
          />
        ) : null}
        <SettingRow
          title={t("Renderer packages")}
          description={installMessage || undefined}
          control={
            <button
              type="button"
              disabled={installing || !status?.runtime.available || status.runtime.installed}
              onClick={() => void install()}
              className="rounded-md border border-[var(--border)] px-3 py-1 text-[12px] hover:bg-[var(--muted)] disabled:opacity-50"
            >
              {installing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : status?.runtime.installed ? (
                t("Installed")
              ) : (
                t("Install now")
              )}
            </button>
          }
        />
        <SettingRow
          title={t("npm registry mirror")}
          description={t(
            "Optional. Used for the first install, for example https://registry.npmmirror.com. Leave empty for the default registry.",
          )}
          control={
            <input
              value={settings.npm_registry}
              onChange={(event) => patch({ npm_registry: event.target.value })}
              placeholder={DEFAULT_NPM_REGISTRY}
              aria-label={t("npm registry mirror")}
              className="w-72 max-w-full rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 font-mono text-[12px] outline-none focus:border-[var(--primary)]"
            />
          }
        />
      </SettingSection>
    </div>
  );
}

function StatusIcon({ ok }: { ok: boolean }) {
  return ok ? (
    <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden />
  ) : (
    <XCircle className="h-4 w-4 text-[var(--muted-foreground)]" aria-hidden />
  );
}

function NumberRow({
  label,
  help,
  value,
  onChange,
  min,
  max,
  step = 1,
  isFloat = false,
}: {
  label: string;
  help?: string;
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  step?: number;
  isFloat?: boolean;
}) {
  return (
    <SettingRow
      title={label}
      description={help}
      control={
        <input
          type="number"
          aria-label={label}
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(event) => {
            const raw = event.target.value;
            if (raw === "") return;
            const n = isFloat ? parseFloat(raw) : parseInt(raw, 10);
            if (!Number.isNaN(n)) onChange(n);
          }}
          className="w-28 rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 text-right text-[12px] outline-none focus:border-[var(--primary)]"
        />
      }
    />
  );
}

function ToggleRow({
  label,
  help,
  value,
  onChange,
}: {
  label: string;
  help?: string;
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <SettingRow
      title={label}
      description={help}
      control={
        <button
          type="button"
          role="switch"
          aria-checked={value}
          aria-label={label}
          onClick={() => onChange(!value)}
          className={
            "relative inline-flex h-5 w-9 items-center rounded-full transition " +
            (value ? "bg-[var(--primary)]" : "bg-[var(--muted)]")
          }
        >
          <span
            className={
              "inline-block h-4 w-4 transform rounded-full bg-white shadow transition " +
              (value ? "translate-x-4" : "translate-x-0.5")
            }
          />
        </button>
      }
    />
  );
}
