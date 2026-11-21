"use client";

import { useState } from "react";
import { Download, ExternalLink, HardDrive } from "lucide-react";
import { useTranslation } from "react-i18next";
import { apiFetch, apiUrl } from "@/shared/api/client";
import { formatBytes } from "@/lib/doc-attachments";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

/**
 * Open / Download controls shared by every file a chat turn generates
 * (generated-file cards, Math Animator renders, video lessons), so each one
 * offers the same actions in the same place. A published Math Animator
 * artifact can also clear its rebuildable render files (upstream 38cf3979d,
 * shared component from bee812e6a). Develop manages storage locations itself,
 * so unlike upstream no server path or "show in file manager" is offered.
 */

const WORKSPACE_ITEM_PATH =
  /^\/files\/workspace-items\/ws_[0-9a-f]{32}\/wsi_[0-9a-f]{32}$/;

interface RenderStorage {
  file_bytes: number;
  cache_bytes: number;
  cache_files: number;
  fingerprint: string;
}

/** Absolute URL for a generated file (API-relative paths go through the API base). */
export function resolveOutputUrl(url: string): string {
  return /^(https?:|blob:|data:)/.test(url) ? url : apiUrl(url);
}

function workspaceItem(url: string): URL | null {
  if (!url.startsWith("/files/")) return null;
  const parsed = URL.canParse(url, "http://workspace.local")
    ? new URL(url, "http://workspace.local")
    : null;
  return parsed && WORKSPACE_ITEM_PATH.test(parsed.pathname) ? parsed : null;
}

/** Whether render-file cleanup can be offered for this artifact URL. */
export function supportsRenderCleanup(url: string): boolean {
  return workspaceItem(url) !== null;
}

async function request(path: string, init?: RequestInit) {
  const response = await apiFetch(path, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || response.statusText);
  return body;
}

export interface OutputFileActionsProps {
  url: string;
  filename?: string;
  /** Opens the file in the app (e.g. the Viewer panel); otherwise a new tab. */
  onOpen?: () => void;
  /** Hide Open where the surrounding card already opens the file. */
  showOpen?: boolean;
  /** Offer render-file cleanup (published Math Animator artifacts). */
  renderFiles?: boolean;
  /** Wrapper classes; defaults to a small gap below the file it belongs to. */
  className?: string;
}

export function OutputFileActions(props: OutputFileActionsProps) {
  // Keyed by URL so storage details never carry over to another file.
  return <OutputFileActionsInner key={props.url} {...props} />;
}

const actionClass =
  "inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:opacity-40";

function OutputFileActionsInner({
  url,
  filename,
  onOpen,
  showOpen = true,
  renderFiles = false,
  className = "mt-1.5",
}: OutputFileActionsProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [storage, setStorage] = useState<RenderStorage | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  if (!url) return null;

  const src = resolveOutputUrl(url);
  const asset = renderFiles ? workspaceItem(url) : null;
  const endpoint = (action: string) =>
    asset ? `${asset.pathname}/${action}${asset.search}` : "";

  async function load(): Promise<RenderStorage | null> {
    setBusy(true);
    setError("");
    try {
      const result = (await request(endpoint("render-storage"))) as RenderStorage;
      setStorage(result);
      return result;
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message
          ? t(cause.message)
          : t("Unable to load render files."),
      );
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function clean() {
    if (!storage) return;
    setBusy(true);
    setError("");
    try {
      const result = await request(endpoint("clean-render-cache"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fingerprint: storage.fingerprint }),
      });
      setStorage({ ...storage, ...result });
      setMessage(
        t("Freed {{size}}. Final output is kept.", {
          size: formatBytes(result.freed_bytes ?? 0),
        }),
      );
    } catch (cause) {
      setError(
        cause instanceof Error && cause.message
          ? t(cause.message)
          : t("Unable to clean render files."),
      );
    } finally {
      setConfirm(false);
      setBusy(false);
    }
  }

  return (
    <div className={`${className} text-[12px]`}>
      <div className="flex flex-wrap items-center gap-1">
        {showOpen ? (
          onOpen ? (
            <button type="button" onClick={onOpen} className={actionClass}>
              <ExternalLink size={12} />
              {t("Open")}
            </button>
          ) : (
            <a href={src} target="_blank" rel="noopener noreferrer" className={actionClass}>
              <ExternalLink size={12} />
              {t("Open in new tab")}
            </a>
          )
        ) : null}
        <a href={src} download={filename || ""} className={actionClass}>
          <Download size={12} />
          {t("Download")}
        </a>
        {asset ? (
          <button
            type="button"
            aria-expanded={open}
            className={actionClass}
            onClick={() => {
              setOpen(!open);
              if (!open) void load();
            }}
          >
            <HardDrive size={12} />
            {t("Render files")}
          </button>
        ) : null}
      </div>
      {asset && open ? (
        <div className="mt-2 space-y-2 rounded-lg border border-[var(--border)] p-3">
          {busy && !storage ? <p role="status">{t("Loading render files…")}</p> : null}
          {error ? (
            <p role="alert" className="text-red-600">
              {error}
            </p>
          ) : null}
          {message ? <p role="status">{message}</p> : null}
          {storage ? (
            <>
              <p>
                {t("Render files: {{count}} files · {{size}}", {
                  count: storage.cache_files ?? 0,
                  size: formatBytes(storage.cache_bytes ?? 0),
                })}
              </p>
              <p className="text-[var(--muted-foreground)]">
                {t("Cleanup keeps the final output, source code, and chat playback.")}
              </p>
              <button
                type="button"
                disabled={busy || !storage.cache_files}
                className="rounded-md border border-[var(--border)] px-2 py-1 text-[12px] hover:bg-[var(--background)] disabled:opacity-40"
                onClick={async () => {
                  setMessage("");
                  const preview = await load();
                  if (preview && (preview.cache_files ?? 0) > 0) setConfirm(true);
                }}
              >
                {t("Clean render files")}
              </button>
            </>
          ) : !busy ? (
            <button
              type="button"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-[12px] hover:bg-[var(--background)]"
              onClick={() => void load()}
            >
              {t("Retry")}
            </button>
          ) : null}
        </div>
      ) : null}
      <ConfirmDialog
        open={confirm}
        title={t("Clean render files?")}
        confirmLabel={t("Clean render files")}
        tone="danger"
        busy={busy}
        onCancel={() => setConfirm(false)}
        onConfirm={() => void clean()}
      >
        <p>
          {t("Remove {{count}} render files and free {{size}}?", {
            count: storage?.cache_files ?? 0,
            size: formatBytes(storage?.cache_bytes ?? 0),
          })}
        </p>
        <p className="mt-2">
          {t("Cleanup keeps the final output, source code, and chat playback.")}
        </p>
      </ConfirmDialog>
    </div>
  );
}
