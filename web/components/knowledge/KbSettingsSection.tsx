"use client";

import { useTranslation } from "react-i18next";
import { useEffect, useState } from "react";
import { ArrowRight, Globe, Lock, Star, Trash2 } from "lucide-react";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { PublishScopeEditor } from "@/components/publishing/PublishScopeEditor";
import { listKnowledgeBaseReferences, type KnowledgeBaseReferenceSummary } from "@/features/knowledge/api/catalog";
import {
  describeOtherReferences,
  runWithWithdrawalConfirmation,
  type WithdrawalImpact,
} from "@/lib/withdrawal-confirmation";
import type { KnowledgeBaseVisibility } from "@/lib/knowledge-helpers";
import { knowledgeBaseRef } from "@/lib/knowledge-helpers";
import {
  listWorkspaces,
  previewKnowledgeMove,
  type ChatWorkspaceRegistration,
  type KnowledgeMovePreview,
} from "@/lib/workspaces-api";
import {
  formatKnowledgeTimestamp,
  isMarginNoteKb,
  providerUsesEmbeddingMetadata,
  type KnowledgeBase,
} from "@/lib/knowledge-helpers";

interface KbSettingsSectionProps {
  kb: KnowledgeBase;
  onSetDefault?: () => Promise<void>;
  onDelete?: () => Promise<void>;
  onMove?: (targetWorkspaceId: string) => Promise<void>;
  /** Rule R1: retried with the confirmed counts after the user accepts the teacher-path impact. */
  onVisibilityChange?: (
    visibility: KnowledgeBaseVisibility, options?: { confirmed?: WithdrawalImpact },
  ) => Promise<void>;
}

export default function KbSettingsSection({
  kb,
  onSetDefault,
  onDelete,
  onMove,
  onVisibilityChange,
}: KbSettingsSectionProps) {
  const { t } = useTranslation();
  const [workspaces, setWorkspaces] = useState<ChatWorkspaceRegistration[]>([]);
  const [targetWorkspaceId, setTargetWorkspaceId] = useState("");
  const [movePreview, setMovePreview] = useState<KnowledgeMovePreview | null>(null);
  const [moveBusy, setMoveBusy] = useState(false);
  const [moveError, setMoveError] = useState<string | null>(null);
  const sourceId = knowledgeBaseRef(kb);
  const moveSourceId = kb.id && !kb.id.startsWith("kb:") ? kb.id : sourceId;
  const sourceWorkspaceId = moveSourceId.startsWith("account:kb:")
    ? ""
    : moveSourceId.startsWith("workspace:")
      ? moveSourceId.slice("workspace:".length).split(":kb:")[0]
      : null;
  const { isTeacher, allowPrivateKb, isAdmin } = useAuthStatus();
  const canWrite = !kb.read_only && allowPrivateKb;
  const canManage = Boolean(kb.kb_id) && canWrite;
  // D10: an administrator withdraws or deletes someone else's published KB, nothing more.
  const canRetire = !canWrite && isAdmin && Boolean(kb.can_retire) && kb.visibility === "public";
  const [references, setReferences] = useState<KnowledgeBaseReferenceSummary | null>(null);
  const [visibilityBusy, setVisibilityBusy] = useState(false);
  const [visibilityError, setVisibilityError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setReferences(null);
    setVisibilityError(null);
    if (canManage) {
      listKnowledgeBaseReferences(sourceId).then((items) => {
        if (alive) setReferences(items);
      }).catch((error) => {
        if (alive) setVisibilityError(error instanceof Error ? error.message : String(error));
      });
    }
    return () => { alive = false; };
  }, [canManage, sourceId, kb.visibility]);

  const changeVisibility = async (next: KnowledgeBaseVisibility) => {
    if (!onVisibilityChange || !(canManage || canRetire)) return;
    setVisibilityBusy(true);
    setVisibilityError(null);
    try {
      // Rule R1: other accounts never block a withdrawal; published teacher
      // paths only need the owner to confirm the (counts-only) impact.
      await runWithWithdrawalConfirmation(
        (confirmed) => (confirmed ? onVisibilityChange(next, { confirmed }) : onVisibilityChange(next)),
        t,
      );
    } catch (error) {
      setVisibilityError(error instanceof Error ? error.message : String(error));
    } finally {
      setVisibilityBusy(false);
    }
  };

  useEffect(() => {
    if (!canWrite || !onMove || sourceWorkspaceId === null) return;
    let alive = true;
    void listWorkspaces().then((items) => { if (alive) setWorkspaces(items); })
      .catch(() => { if (alive) setWorkspaces([]); });
    return () => { alive = false; };
  }, [canWrite, onMove, sourceWorkspaceId]);

  const reviewMove = async () => {
    setMoveBusy(true);
    setMoveError(null);
    try {
      setMovePreview(await previewKnowledgeMove(moveSourceId, targetWorkspaceId));
    } catch (error) {
      setMoveError(error instanceof Error ? error.message : String(error));
    } finally {
      setMoveBusy(false);
    }
  };

  const commitMove = async () => {
    setMoveBusy(true);
    setMoveError(null);
    try {
      await onMove?.(targetWorkspaceId);
    } catch (error) {
      setMoveError(error instanceof Error ? error.message : String(error));
    } finally {
      setMoveBusy(false);
    }
  };
  const meta = kb.metadata || {};
  // A MarginNote library runs no engine and no embedding, and its `path` is
  // a name rather than a folder that exists — reporting the ordinary fields
  // described a pipeline and a directory it never has.
  const isMarginNote = isMarginNoteKb(kb);
  const provider = isMarginNote
    ? t("MarginNote 4")
    : kb.statistics?.rag_provider || "llamaindex";
  const pageIndexProvider =
    isMarginNote || !providerUsesEmbeddingMetadata(provider);
  const embeddingLabel = meta.embedding_model
    ? typeof meta.embedding_dim === "number"
      ? `${meta.embedding_model} · ${meta.embedding_dim}${t("d")}`
      : meta.embedding_model
    : t("Default embedding");
  const created = formatKnowledgeTimestamp(meta.created_at);
  const updated = formatKnowledgeTimestamp(meta.last_updated);
  const lastIndexed = formatKnowledgeTimestamp(meta.last_indexed_at);

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div>
          <div className="text-[13px] font-medium text-[var(--foreground)]">
            {t("Overview")}
          </div>
          <p className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">
            {t("Read-only metadata. Use the actions below to manage this KB.")}
          </p>
        </div>

        <dl className="grid gap-3 rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 sm:grid-cols-2">
          <Field label={t("RAG provider")}>{provider}</Field>
          {!pageIndexProvider && (
            <Field label={t("Embedding")}>{embeddingLabel}</Field>
          )}
          <Field label={t("Created")}>{created || "—"}</Field>
          <Field label={t("Updated")}>{updated || "—"}</Field>
          {!isMarginNote && (
            <Field label={t("Last indexed")}>{lastIndexed || "—"}</Field>
          )}
          {isMarginNote
            ? meta.db_path && (
                <Field label={t("Synced store")} className="sm:col-span-2">
                  <span className="font-mono text-[10.5px] text-[var(--muted-foreground)]">
                    {meta.db_path}
                  </span>
                </Field>
              )
            : kb.path && (
                <Field label={t("On-disk path")} className="sm:col-span-2">
                  <span className="font-mono text-[10.5px] text-[var(--muted-foreground)]">
                    {kb.path}
                  </span>
                </Field>
              )}
        </dl>
      </section>

      {(kb.is_default || (canWrite && kb.can_set_default && onSetDefault)) && <section className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--background)] p-3">
        <div>
          <div className="text-[12.5px] font-medium text-[var(--foreground)]">
            {t("Default knowledge base")}
          </div>
          <p className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">
            {t("The default KB is selected automatically in chat & partners.")}
          </p>
        </div>
        {kb.is_default ? (
          <span className="inline-flex items-center gap-1.5 rounded-md bg-amber-100 px-2.5 py-1 text-[12px] font-medium text-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
            <Star className="h-3 w-3" fill="currentColor" />
            {t("Currently default")}
          </span>
        ) : (
          <button
            type="button"
            onClick={() => void onSetDefault?.()}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] bg-[var(--background)] px-2.5 py-1 text-[12px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]"
          >
            <Star className="h-3 w-3" />
            {t("Set as default")}
          </button>
        )}
      </section>}

      {/* Spec 2026-10-29 D4: moving between workspaces is for administrators. */}
      {canWrite && onMove && sourceWorkspaceId !== null && isAdmin && (
        <section className="space-y-3 rounded-lg border border-[var(--border)] bg-[var(--background)] p-3">
          <div>
            <div className="text-[12.5px] font-medium text-[var(--foreground)]">
              {t("Move knowledge base")}
            </div>
            <p className="mt-0.5 text-[11.5px] text-[var(--muted-foreground)]">
              {t("Move this knowledge base with its documents, settings, and indexes. Existing workspace assignments are updated.")}
            </p>
          </div>
          <select
            value={targetWorkspaceId}
            onChange={(event) => {
              setTargetWorkspaceId(event.target.value);
              setMovePreview(null);
            }}
            disabled={moveBusy}
            className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2.5 py-1.5 text-[12px]"
          >
            <option value="" disabled={sourceWorkspaceId === ""}>{t("Account library")}</option>
            {workspaces
              .filter((row) => row.kind === "workspace" && !row.archived && row.status === "ready" && row.workspace_id !== sourceWorkspaceId)
              .map((row) => (
                <option key={row.workspace_id} value={row.workspace_id}>{row.display_name}</option>
              ))}
          </select>
          <button
            type="button"
            disabled={moveBusy || targetWorkspaceId === sourceWorkspaceId}
            onClick={() => void reviewMove()}
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-[12px] disabled:opacity-50"
          >
            {t("Review move")}
          </button>
          {movePreview && (
            <div className="space-y-2 text-[11.5px] text-[var(--muted-foreground)]">
              <p>{t("{{count}} files · {{bytes}} bytes", { count: movePreview.files, bytes: movePreview.bytes })}</p>
              {movePreview.assignments.length > 0 && (
                <p>{t("Assignments updated: {{names}}", { names: movePreview.assignments.map((row) => row.display_name).join(", ") })}</p>
              )}
              {movePreview.blockers.map((blocker) => (
                <p key={blocker} role="alert" className="text-red-600">{blocker}</p>
              ))}
              {movePreview.blockers.length === 0 && (
                <button
                  type="button"
                  disabled={moveBusy}
                  onClick={() => void commitMove()}
                  className="inline-flex items-center gap-1 rounded-md bg-[var(--primary)] px-2.5 py-1 text-white disabled:opacity-50"
                >
                  <ArrowRight className="h-3 w-3" />
                  {t("Move knowledge base")}
                </button>
              )}
            </div>
          )}
          {moveError && <p role="alert" className="text-[11.5px] text-red-600">{moveError}</p>}
        </section>
      )}

      {kb.kb_id && <section className="space-y-2 border-t border-[var(--border)] pt-4">
        <h3 className="text-sm font-medium">{t("Visibility")}</h3>
        <p className="text-xs text-[var(--muted-foreground)]">
          {kb.connector ? t("Connectors are always private.") : kb.visibility === "public"
            ? t("Published knowledge bases are readable by the people chosen below and usable in their chat.")
            : t("Only you can see this knowledge base. Items that reference it can still read it.")}
        </p>
        {((canManage && (kb.visibility === "public" || isTeacher)) || canRetire) && onVisibilityChange && !kb.connector && (
          <button type="button" disabled={visibilityBusy || (canManage && references === null)}
            onClick={() => void changeVisibility(kb.visibility === "public" ? "private" : "public")}
            className="inline-flex items-center gap-1.5 rounded-md border border-[var(--border)] px-2.5 py-1 text-xs disabled:opacity-50">
            {kb.visibility === "public" ? <Lock className="h-3 w-3" /> : <Globe className="h-3 w-3" />}
            {kb.visibility === "public" ? t("Make private") : t("Make public")}
          </button>
        )}
        {canManage && kb.visibility === "public" && !kb.connector && (
          <div className="rounded-lg border border-[var(--border)] p-3">
            <PublishScopeEditor kind="knowledge_base" itemId={kb.kb_id} />
          </div>
        )}
        {canManage && references !== null && <div className="text-xs text-[var(--muted-foreground)]">
          <h4 className="font-medium">{t("Referenced by")}</h4>
          {references.references.length === 0 && references.other_references.total === 0
            ? <p>{t("Not referenced anywhere.")}</p>
            : <>
              {references.references.length > 0 && <ul className="mt-1 list-disc break-all pl-4">{references.references.map((item) => (
                <li key={`${item.referrer_kind}:${item.referrer_id}`}>{item.referrer_kind}: {item.referrer_id}</li>
              ))}</ul>}
              {references.other_references.total > 0 && <p className="mt-1">{describeOtherReferences(references.other_references, t)}</p>}
            </>}
        </div>}
        {visibilityError && <p role="alert" className="text-xs text-red-600">{visibilityError}</p>}
      </section>}

      {(canWrite || canRetire) && onDelete && <section className="space-y-3 rounded-lg border border-red-200 bg-red-50/40 p-3 dark:border-red-900/60 dark:bg-red-950/15">
        <div>
          <div className="text-[12.5px] font-medium text-red-700 dark:text-red-300">
            {t("Danger zone")}
          </div>
          <p className="mt-0.5 text-[11.5px] text-red-700/80 dark:text-red-300/80">
            {isMarginNote
              ? t(
                  "Deleting this library removes its synced objects and unpairs every device. Nothing in MarginNote 4 itself is touched.",
                )
              : t(
                  "Deleting a knowledge base permanently removes its raw documents and index versions.",
                )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void onDelete()}
          className="inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-red-50 px-2.5 py-1 text-[12px] font-medium text-red-700 transition-colors hover:bg-red-100 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300 dark:hover:bg-red-950/50"
        >
          <Trash2 className="h-3 w-3" />
          {t("Delete knowledge base")}
        </button>
      </section>}
    </div>
  );
}

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <dt className="text-[10.5px] uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
        {label}
      </dt>
      <dd className="mt-1 text-[12.5px] text-[var(--foreground)]">
        {children}
      </dd>
    </div>
  );
}
