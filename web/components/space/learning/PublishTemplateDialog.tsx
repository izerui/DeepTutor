"use client";

import { Fragment, useEffect, useMemo, useState, type RefObject } from "react";
import { AlertTriangle, Loader2, RefreshCw, Send, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useModalDialog } from "@/hooks/useModalDialog";
import type { MasteryTopic } from "@/lib/learning-api";
import {
  listAssignments,
  previewPublish,
  publishTemplate,
  registerTeacherTemplate,
  type KbChoice,
  type KpDecision,
  type PublishBlockedDetail,
  type PublishPreview,
  type PublishWarning,
  type TeacherTemplate,
} from "@/lib/teaching-api";

import { topicDisplayName, type Translate } from "./format";
import { StudentPicker } from "./StudentPicker";
import {
  canSubmitPublish,
  decisionValue,
  duplicateContinuations,
  keepChoices,
  keepKpDecisions,
  parseDecision,
  publishBlocked,
  publishStepMessage,
  sourceIssueMessage,
  teachingErrorMessage,
  type Message,
} from "./teaching";

const CHOICES = ["make_public", "reference_only"] as const;

export function PublishTemplateDialog({
  topic,
  template,
  returnFocusRef,
  onClose,
  onPublished,
}: {
  topic: MasteryTopic;
  /** Absent the first time: the dialog registers the topic as a template. */
  template?: TeacherTemplate;
  returnFocusRef: RefObject<HTMLElement | null>;
  onClose: () => void;
  onPublished: (template: TeacherTemplate) => void;
}) {
  const { t } = useTranslation();
  const say = (message: Message) => t(message.key, message.values);
  const [templateId, setTemplateId] = useState(template?.id ?? "");
  const [preview, setPreview] = useState<PublishPreview | null>(null);
  const [assignedIds, setAssignedIds] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<Message | null>(null);
  const [submitError, setSubmitError] = useState<Message | null>(null);
  const [blocked, setBlocked] = useState<PublishBlockedDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState(0);
  const [kbChoices, setKbChoices] = useState<Record<string, KbChoice>>({});
  const [bookChoices, setBookChoices] = useState<Record<string, KbChoice>>({});
  const [decisions, setDecisions] = useState<Record<string, KpDecision>>({});
  const [published, setPublished] = useState<{
    template: TeacherTemplate;
    warnings: PublishWarning[];
  } | null>(null);
  const [studentIds, setStudentIds] = useState<string[]>([]);
  const dialogRef = useModalDialog(onClose, busy, returnFocusRef);

  const knownTemplateId = template?.id;
  const pathId = topic.path_id;
  const workspaceId = topic.content_workspace_id ?? "";

  useEffect(() => {
    // Choices belong to one path: another topic/template starts from defaults.
    setKbChoices({});
    setBookChoices({});
    setDecisions({});
  }, [knownTemplateId, pathId, workspaceId]);

  useEffect(() => {
    // Every check starts clean, and only the newest one may fill the dialog:
    // a slow answer for an earlier check (or another topic) is dropped.
    let alive = true;
    setPreview(null);
    setLoadError(null);
    setSubmitError(null);
    setBlocked(null);
    (async () => {
      try {
        const registered = knownTemplateId
          ? { id: knownTemplateId }
          : await registerTeacherTemplate(pathId, workspaceId);
        const [next, assignments] = await Promise.all([
          previewPublish(registered.id),
          listAssignments(registered.id).catch(() => []),
        ]);
        if (!alive) return;
        setTemplateId(registered.id);
        setPreview(next);
        setAssignedIds(
          assignments.filter((row) => row.status === "active").map((row) => row.student_user_id),
        );
        // Defaults come from the server: "make public" the first time, the
        // previous revision's choice on a republish. "Check again" keeps the
        // teacher's own choices for keys that still exist (never silently
        // turning a reference-only material public); only new keys default.
        setKbChoices((current) =>
          keepChoices(
            current,
            Object.fromEntries(next.private_kbs.map((kb) => [kb.kb_id, kb.default])),
          ),
        );
        setBookChoices((current) =>
          keepChoices(
            current,
            Object.fromEntries(next.private_books.map((book) => [book.book_id, book.default])),
          ),
        );
        setDecisions((current) =>
          keepKpDecisions(
            current,
            next.changes.items,
            next.changes.removed.map((point) => point.kp_id),
          ),
        );
      } catch (reason) {
        if (alive) setLoadError(teachingErrorMessage(reason));
      }
    })();
    return () => {
      alive = false;
    };
  }, [knownTemplateId, pathId, workspaceId, check]);

  const sourceKinds = useMemo(
    () => Object.fromEntries(topic.sources.map((source) => [source.id, String(source.kind)])),
    [topic.sources],
  );
  const materialNames = useMemo(
    () => ({
      ...Object.fromEntries((preview?.private_kbs ?? []).map((kb) => [kb.kb_id, kb.name])),
      ...Object.fromEntries((preview?.private_books ?? []).map((book) => [book.book_id, book.name])),
    }),
    [preview],
  );

  // A refused publish reports what blocks it now; otherwise the preview does.
  const issues = blocked ?? preview;
  const outlineIssues = issues?.outline_issues ?? [];
  const sourceIssues = (issues?.source_issues ?? []).filter(
    (issue) => issue.code !== "publish_step_failed",
  );
  const failedStep = blocked?.failed_step;
  const hasBlockers = outlineIssues.length > 0 || sourceIssues.length > 0 || Boolean(failedStep);
  const firstPublish = preview?.changes.first_publish ?? !template?.current_revision;
  const removed = preview?.changes.removed ?? [];
  const removedNames = Object.fromEntries(removed.map((point) => [point.kp_id, point.name]));
  const duplicates = duplicateContinuations(decisions);
  const canSubmit = !blocked && !published && canSubmitPublish(preview, decisions, busy);

  const submit = async () => {
    if (!preview || !canSubmit) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const result = await publishTemplate(templateId, {
        draft_revision: preview.draft_revision,
        kp_decisions: decisions,
        kb_choices: kbChoices,
        book_choices: bookChoices,
        student_ids: studentIds,
      });
      // Published, but something after the revision failed: say so and let
      // the teacher close the dialog themselves.
      if (result.warnings.length > 0) setPublished(result);
      else onPublished(result.template);
    } catch (reason) {
      const detail = publishBlocked(reason);
      if (detail) setBlocked(detail);
      else setSubmitError(teachingErrorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const recheck = () => setCheck((value) => value + 1);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[var(--overlay)] p-3 backdrop-blur-[2px] sm:p-5">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-template-title"
        aria-busy={busy || (!preview && !loadError)}
        tabIndex={-1}
        className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-2xl outline-none sm:max-h-[85vh]"
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--border)] p-4 sm:p-5">
          <div className="min-w-0">
            <h2 id="publish-template-title" className="text-base font-semibold">
              {firstPublish ? t("Publish to students") : t("Publish update")}
            </h2>
            <p className="mt-1 truncate text-sm text-[var(--muted-foreground)]">
              {topicDisplayName(topic, t as Translate)}
            </p>
          </div>
          <button
            type="button"
            onClick={published ? () => onPublished(published.template) : onClose}
            disabled={busy}
            aria-label={t("Close")}
            className="rounded-lg p-1.5 text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-40"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain p-4 text-sm sm:p-5">
          {!preview && !loadError && (
            <div role="status" className="flex items-center gap-2 text-[var(--muted-foreground)]">
              <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
              {t("Checking this path before publishing…")}
            </div>
          )}

          {loadError && (
            <p role="alert" className="text-xs text-red-600">
              {say(loadError)}
            </p>
          )}

          {published && (
            <section
              role="alert"
              className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3"
            >
              <h3 className="font-medium">{t("Published, but some steps need attention")}</h3>
              <p className="mt-1 text-xs">
                {t("Published v{{revision}}", { revision: published.template.current_revision })}
              </p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
                {published.warnings.map((warning, index) => (
                  <li key={`${warning.step}:${index}`}>
                    {t("{{step}} did not complete.", {
                      step: say(publishStepMessage(warning.step, materialNames)),
                    })}
                    <span lang="en" className="block text-[var(--muted-foreground)]">
                      {warning.message}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {hasBlockers && !published && (
            <section
              role={blocked ? "alert" : undefined}
              aria-labelledby="publish-blockers-title"
              className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3"
            >
              <h3 id="publish-blockers-title" className="flex items-center gap-1.5 font-medium">
                <AlertTriangle aria-hidden className="h-4 w-4" />
                {t("Fix these before publishing")}
              </h3>
              <ul className="mt-2 list-disc space-y-1.5 pl-5 text-xs">
                {failedStep && (
                  <li>
                    {t("Publishing stopped; no new version was published.")}{" "}
                    {t("It stopped at: {{step}}", {
                      step: say(publishStepMessage(failedStep, materialNames)),
                    })}
                    {(blocked?.completed_steps ?? []).length > 0 && (
                      <span className="block">
                        {t("Already done before it stopped: {{steps}}", {
                          steps: (blocked?.completed_steps ?? [])
                            .map((step) => say(publishStepMessage(step, materialNames)))
                            .join(" · "),
                        })}
                      </span>
                    )}
                  </li>
                )}
                {sourceIssues.map((issue, index) => (
                  <li key={`${issue.source_id}:${issue.code}:${index}`}>
                    {say(sourceIssueMessage(issue, sourceKinds))}
                    {issue.code === "book_kb_blocked" && (
                      <span lang="en" className="block text-[var(--muted-foreground)]">
                        {issue.message}
                      </span>
                    )}
                  </li>
                ))}
                {outlineIssues.length > 0 && (
                  <li>
                    {t("The outline or the change decisions cannot be published yet. Fix them, then check again.")}
                    <ul lang="en" className="mt-1 list-[circle] pl-4 text-[var(--muted-foreground)]">
                      {outlineIssues.map((message, index) => (
                        <li key={`${index}:${message}`}>{message}</li>
                      ))}
                    </ul>
                  </li>
                )}
              </ul>
            </section>
          )}

          {preview && !published && preview.private_books.length > 0 && (
            <section>
              <h3 className="font-medium">{t("Private books")}</h3>
              {preview.private_books.map((book) => (
                <fieldset
                  key={book.book_id}
                  disabled={busy}
                  className="mt-2 rounded-lg border border-[var(--border)] p-3"
                >
                  <legend className="px-1 text-xs font-medium">{book.name}</legend>
                  {CHOICES.map((choice) => (
                    <label key={choice} className="flex min-h-8 items-center gap-2 py-1 text-xs">
                      <input
                        type="radio"
                        name={`book-${book.book_id}`}
                        checked={(bookChoices[book.book_id] ?? book.default) === choice}
                        onChange={() =>
                          setBookChoices((previous) => ({ ...previous, [book.book_id]: choice }))
                        }
                      />
                      {choice === "make_public"
                        ? t("Make the book public")
                        : t("Readable through this path only")}
                    </label>
                  ))}
                </fieldset>
              ))}
            </section>
          )}

          {preview && !published && preview.private_kbs.length > 0 && (
            <section>
              <h3 className="font-medium">{t("Private knowledge bases")}</h3>
              {preview.private_kbs.map((kb) => (
                <fieldset
                  key={kb.kb_id}
                  disabled={busy}
                  className="mt-2 rounded-lg border border-[var(--border)] p-3"
                >
                  <legend className="px-1 text-xs font-medium">
                    {kb.via_name
                      ? t("{{name}} (cited by {{book}})", { name: kb.name, book: kb.via_name })
                      : kb.name}
                  </legend>
                  {CHOICES.map((choice) => (
                    <label key={choice} className="flex min-h-8 items-center gap-2 py-1 text-xs">
                      <input
                        type="radio"
                        name={`kb-${kb.kb_id}`}
                        checked={(kbChoices[kb.kb_id] ?? kb.default) === choice}
                        onChange={() =>
                          setKbChoices((previous) => ({ ...previous, [kb.kb_id]: choice }))
                        }
                      />
                      {choice === "make_public"
                        ? t("Make the knowledge base public")
                        : t("Readable through this path only")}
                    </label>
                  ))}
                </fieldset>
              ))}
            </section>
          )}

          {preview && !published && !firstPublish && (
            <section>
              <h3 className="font-medium">{t("What changed")}</h3>
              <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                {t("{{count}} students will get this update the next time they study", {
                  count: preview.affected_students,
                })}
              </p>
              {preview.changes.items.length > 0 && (
                <ul className="mt-2 divide-y divide-[var(--border)] rounded-lg border border-[var(--border)]">
                  {preview.changes.items.map((item) => (
                    <li
                      key={item.kp_id}
                      className="flex flex-col gap-1.5 px-3 py-2 text-xs sm:flex-row sm:items-center sm:justify-between sm:gap-3"
                    >
                      <span className="min-w-0">
                        <span className="block break-words font-medium">{item.name}</span>
                        {item.reason && (
                          <span className="block text-[var(--muted-foreground)]">
                            {item.reason}
                          </span>
                        )}
                      </span>
                      <select
                        aria-label={item.name}
                        disabled={busy}
                        value={decisionValue(decisions[item.kp_id])}
                        onChange={(event) =>
                          setDecisions((previous) => ({
                            ...previous,
                            [item.kp_id]: parseDecision(event.target.value),
                          }))
                        }
                        className="h-8 w-full rounded-md border border-[var(--input)] bg-[var(--background)] px-2 sm:w-auto sm:max-w-[60%]"
                      >
                        {item.status === "kept" ? (
                          <>
                            <option value="unchanged">{t("Unchanged")}</option>
                            <option value="reworded">{t("Reworded")}</option>
                            <option value="redefined">{t("Learning goal changed")}</option>
                          </>
                        ) : (
                          <>
                            <option value="added">{t("New knowledge point")}</option>
                            {removed.map((old) => (
                              <Fragment key={old.kp_id}>
                                <option value={`reworded:${old.kp_id}`}>
                                  {t("Reworded")}: {old.name}
                                </option>
                                <option value={`redefined:${old.kp_id}`}>
                                  {t("Learning goal changed")}: {old.name}
                                </option>
                              </Fragment>
                            ))}
                          </>
                        )}
                      </select>
                    </li>
                  ))}
                </ul>
              )}
              {removed.length > 0 && (
                <p className="mt-2 text-xs text-[var(--muted-foreground)]">
                  {removed.map((old) => t("Removed: {{name}}", { name: old.name })).join(" · ")}
                </p>
              )}
              {duplicates.length > 0 && (
                <p role="alert" className="mt-2 text-xs text-red-600">
                  {t("More than one new knowledge point continues {{name}}. Choose one.", {
                    name: duplicates.map((kpId) => removedNames[kpId] ?? kpId).join(", "),
                  })}
                </p>
              )}
            </section>
          )}

          {preview && !published && (
            <section>
              <h3 className="font-medium">{t("Students")}</h3>
              {assignedIds.length > 0 && (
                <p className="mt-1 text-xs text-[var(--muted-foreground)]">
                  {t("{{count}} students assigned", { count: assignedIds.length })}
                </p>
              )}
              <StudentPicker
                selected={studentIds}
                onChange={setStudentIds}
                exclude={assignedIds}
                disabled={busy}
              />
            </section>
          )}

          {submitError && (
            <p role="alert" className="text-xs text-red-600">
              {say(submitError)}
            </p>
          )}
        </div>

        <footer className="flex flex-wrap justify-end gap-2 border-t border-[var(--border)] p-4">
          {published ? (
            <button
              type="button"
              onClick={() => onPublished(published.template)}
              className="inline-flex h-9 items-center rounded-lg bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2"
            >
              {t("Close")}
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="h-9 rounded-lg px-3 text-sm text-[var(--muted-foreground)] hover:bg-[var(--accent)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
              >
                {t("Cancel")}
              </button>
              {(blocked || submitError || loadError) && (
                <button
                  type="button"
                  onClick={recheck}
                  disabled={busy}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:opacity-50"
                >
                  <RefreshCw aria-hidden className="h-4 w-4" />
                  {t("Check again")}
                </button>
              )}
              <button
                type="button"
                onClick={() => void submit()}
                disabled={!canSubmit}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-[var(--primary)] px-4 text-sm font-medium text-[var(--primary-foreground)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] focus-visible:ring-offset-2 disabled:opacity-50"
              >
                {busy ? (
                  <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                ) : (
                  <Send aria-hidden className="h-4 w-4" />
                )}
                {t("Publish")}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  );
}
