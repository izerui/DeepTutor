"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Archive, BookOpen, Plus, Save, Send, Sparkles, Trash2, X } from "lucide-react";

import { actionClass, inputClass, primaryClass } from "@/components/classes/ClassesHub";
import QuestionBankPicker from "@/components/chat/QuestionBankPicker";
import { PublishScopeEditor } from "@/components/publishing/PublishScopeEditor";
import { listKnowledgeBases } from "@/features/knowledge/api/catalog";
import {
  archiveSet,
  deleteSet,
  generateQuestions,
  getReport,
  getSet,
  publishSet,
  questionsFromBank,
  updateSet,
  type PracticeQuestion,
  type PracticeQuestionType,
  type PracticeReport,
  type PracticeSetDraft,
  type Reveal,
} from "@/lib/practice-sets-api";
import { formatDate } from "./PracticeSetsHub";

const sectionClass = "space-y-3 rounded-xl border border-[var(--border)] p-4";
const titleClass = "text-[13.5px] font-medium text-[var(--foreground)]";
const hintClass = "text-[12px] leading-relaxed text-[var(--muted-foreground)]";

const TYPE_LABELS: Record<PracticeQuestionType, string> = {
  choice: "Single choice",
  multi_choice: "Multiple choice",
  concept: "True or false",
  fill_in_blank: "Fill in the blank",
  short_answer: "Short answer",
  written: "Written answer",
  coding: "Coding",
};

const blankQuestion = (): PracticeQuestion => ({
  question_id: "",
  question: "",
  question_type: "choice",
  options: { A: "", B: "", C: "", D: "" },
  correct_answer: "",
  explanation: "",
  points: 1,
});

/** dd/mm inputs speak local time; the API speaks seconds since the epoch. */
const toLocalInput = (seconds: number | null) => {
  if (!seconds) return "";
  const date = new Date(seconds * 1000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};

/** A teacher's practice set: draft, publish, scope and report (spec 2026-10-29 §8). */
export function SetEditor({
  setId,
  onChanged,
  onDeleted,
}: {
  setId: string;
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState<PracticeSetDraft | null>(null);
  const [draft, setDraft] = useState<PracticeSetDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [picking, setPicking] = useState(false);

  useEffect(() => {
    let alive = true;
    getSet(setId)
      .then((next) => {
        if (!alive) return;
        setSaved(next);
        setDraft(next);
      })
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [setId, t]);

  if (!draft || !saved) {
    return error ? (
      <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>
    ) : (
      <div aria-busy="true" className="h-60 animate-pulse rounded-xl bg-[var(--muted)]" />
    );
  }

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const run = async (action: () => Promise<PracticeSetDraft | void>, message = "") => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await action();
      if (next) {
        setSaved(next);
        setDraft(next);
      }
      onChanged();
      setNotice(message);
    } catch (err) {
      setError(err instanceof Error ? t(err.message) : String(err));
    } finally {
      setBusy(false);
    }
  };
  const save = () =>
    updateSet(setId, {
      title: draft.title,
      description: draft.description,
      questions: draft.questions,
      reveal: draft.reveal,
      due_at: draft.due_at,
    });
  const patch = (update: Partial<PracticeSetDraft>) => setDraft({ ...draft, ...update });
  const setQuestion = (index: number, question: PracticeQuestion) =>
    patch({ questions: draft.questions.map((q, i) => (i === index ? question : q)) });
  const addQuestions = (questions: PracticeQuestion[]) => patch({ questions: [...draft.questions, ...questions] });

  return (
    <fieldset disabled={busy} className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input
          className={`${inputClass} max-w-md text-[15px] font-semibold`}
          value={draft.title}
          maxLength={200}
          aria-label={t("Title")}
          onChange={(e) => patch({ title: e.target.value })}
        />
        <div className="flex flex-wrap gap-2">
          <button type="button" className={actionClass} disabled={!dirty} onClick={() => void run(save, t("Saved."))}>
            <Save size={13} />
            {t("Save draft")}
          </button>
          <button
            type="button"
            className={primaryClass}
            disabled={draft.questions.length === 0 || draft.status === "archived"}
            onClick={() =>
              void run(async () => {
                await save();
                return publishSet(setId);
              }, t("Published. Choose who receives it below."))
            }
          >
            <Send size={13} />
            {saved.current_revision > 0 ? t("Publish new version") : t("Publish")}
          </button>
        </div>
      </div>
      <p className={hintClass}>
        {saved.current_revision > 0
          ? t("Published v{{revision}}. Changes reach students only after you publish a new version; earlier submissions keep their version.", { revision: saved.current_revision })
          : t("Draft. Students see nothing until you publish.")}
      </p>
      {error && <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>}
      {notice && <p role="status" className="text-[12px] text-[var(--muted-foreground)]">{notice}</p>}

      <section className={sectionClass}>
        <textarea
          className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-3 text-[13px]"
          rows={2}
          maxLength={4000}
          placeholder={t("Instructions for students (optional)")}
          value={draft.description}
          onChange={(e) => patch({ description: e.target.value })}
        />
        <div className="flex flex-wrap gap-4 text-[12.5px]">
          <label className="flex items-center gap-2">
            {t("Deadline (optional)")}
            <input
              type="datetime-local"
              className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
              value={toLocalInput(draft.due_at)}
              onChange={(e) => patch({ due_at: e.target.value ? new Date(e.target.value).getTime() / 1000 : null })}
            />
          </label>
          <label className="flex items-center gap-2">
            {t("Show answers")}
            <select
              className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
              value={draft.reveal}
              onChange={(e) => patch({ reveal: e.target.value as Reveal })}
            >
              <option value="after_submit">{t("Right after submitting")}</option>
              <option value="after_due">{t("After the deadline")}</option>
            </select>
          </label>
        </div>
        <p className={hintClass}>{t("Late submissions are still accepted and marked late.")}</p>
      </section>

      <section className={sectionClass}>
        <h3 className={titleClass}>
          {t("Questions")} <span className="text-[var(--muted-foreground)]">({draft.questions.length})</span>
        </h3>
        <ol className="space-y-3">
          {draft.questions.map((question, index) => (
            <QuestionEditor
              key={`${question.question_id}-${index}`}
              index={index}
              question={question}
              onChange={(next) => setQuestion(index, next)}
              onRemove={() => patch({ questions: draft.questions.filter((_, i) => i !== index) })}
            />
          ))}
        </ol>
        <div className="flex flex-wrap gap-2 border-t border-[var(--border)] pt-3">
          <button type="button" className={actionClass} onClick={() => addQuestions([blankQuestion()])}>
            <Plus size={13} />
            {t("Add a question")}
          </button>
          <button type="button" className={actionClass} onClick={() => setPicking(true)}>
            <BookOpen size={13} />
            {t("Pick from my question bank")}
          </button>
        </div>
        <GeneratePanel onGenerated={addQuestions} onError={setError} />
      </section>

      {saved.current_revision > 0 && (
        <section className={sectionClass}>
          <PublishScopeEditor kind="practice_set" itemId={setId} onSaved={onChanged} />
        </section>
      )}

      {saved.current_revision > 0 && <ReportSection setId={setId} />}

      <div className="flex flex-wrap gap-2">
        {saved.current_revision === 0 ? (
          <button
            type="button"
            className={actionClass}
            onClick={() => {
              if (window.confirm(t("Delete this practice set?"))) void run(() => deleteSet(setId).then(onDeleted));
            }}
          >
            <Trash2 size={13} />
            {t("Delete")}
          </button>
        ) : (
          saved.status !== "archived" && (
            <button
              type="button"
              className={actionClass}
              onClick={() => {
                if (window.confirm(t("Archive this practice set? Students can no longer answer it; results are kept."))) {
                  void run(() => archiveSet(setId));
                }
              }}
            >
              <Archive size={13} />
              {t("Archive")}
            </button>
          )
        )}
      </div>

      <QuestionBankPicker
        open={picking}
        initialSelected={[]}
        onClose={() => setPicking(false)}
        onApply={(entries) => {
          setPicking(false);
          if (entries.length === 0) return;
          void questionsFromBank(entries.map((entry) => entry.id))
            .then(addQuestions)
            .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)));
        }}
      />
    </fieldset>
  );
}

function QuestionEditor({
  index,
  question,
  onChange,
  onRemove,
}: {
  index: number;
  question: PracticeQuestion;
  onChange: (question: PracticeQuestion) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const set = (update: Partial<PracticeQuestion>) => onChange({ ...question, ...update });
  const hasOptions = question.question_type === "choice" || question.question_type === "multi_choice";
  return (
    <li className="space-y-2 rounded-lg border border-[var(--border)] p-3 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{index + 1}.</span>
        <select
          aria-label={t("Question type")}
          className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
          value={question.question_type}
          onChange={(e) => {
            const kind = e.target.value as PracticeQuestionType;
            const choice = kind === "choice" || kind === "multi_choice";
            set({
              question_type: kind,
              options: choice ? (Object.keys(question.options).length ? question.options : blankQuestion().options) : {},
            });
          }}
        >
          {(Object.keys(TYPE_LABELS) as PracticeQuestionType[]).map((kind) => (
            <option key={kind} value={kind}>{t(TYPE_LABELS[kind])}</option>
          ))}
        </select>
        <label className="flex items-center gap-1">
          {t("Points")}
          <input
            type="number"
            min={0}
            max={100}
            step={0.5}
            className="h-8 w-16 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            value={question.points}
            onChange={(e) => set({ points: Number(e.target.value) || 0 })}
          />
        </label>
        <button type="button" className="ml-auto text-[var(--muted-foreground)] hover:text-red-600" aria-label={t("Remove question {{n}}", { n: index + 1 })} onClick={onRemove}>
          <X size={14} />
        </button>
      </div>
      <textarea
        aria-label={t("Question")}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-2"
        rows={2}
        value={question.question}
        onChange={(e) => set({ question: e.target.value })}
      />
      {hasOptions && (
        <div className="grid gap-1 sm:grid-cols-2">
          {Object.entries(question.options).map(([key, text]) => (
            <label key={key} className="flex items-center gap-1.5">
              <span className="w-4 font-medium">{key}</span>
              <input
                className="h-8 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
                value={text}
                onChange={(e) => set({ options: { ...question.options, [key]: e.target.value } })}
              />
            </label>
          ))}
        </div>
      )}
      <label className="flex items-center gap-2">
        <span className="shrink-0">{t("Answer")}</span>
        {question.question_type === "concept" ? (
          <select
            className="h-8 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            value={question.correct_answer ?? ""}
            onChange={(e) => set({ correct_answer: e.target.value })}
          >
            <option value="">—</option>
            <option value="True">{t("True")}</option>
            <option value="False">{t("False")}</option>
          </select>
        ) : (
          <input
            className="h-8 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            placeholder={hasOptions ? t("Option keys, e.g. B or A,C") : t("Reference answer for grading")}
            value={question.correct_answer ?? ""}
            onChange={(e) => set({ correct_answer: e.target.value })}
          />
        )}
      </label>
      <textarea
        aria-label={t("Explanation")}
        placeholder={t("Explanation (optional)")}
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-2"
        rows={1}
        value={question.explanation ?? ""}
        onChange={(e) => set({ explanation: e.target.value })}
      />
    </li>
  );
}

function GeneratePanel({
  onGenerated,
  onError,
}: {
  onGenerated: (questions: PracticeQuestion[]) => void;
  onError: (message: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [kbRef, setKbRef] = useState("");
  const [kbs, setKbs] = useState<{ ref: string; name: string }[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    listKnowledgeBases({ library: true })
      .then((rows) => {
        if (!alive) return;
        setKbs(
          rows.map((row) => {
            const extra = row as { ref?: string; kb_id?: string };
            return { ref: extra.ref || row.id || row.name, name: row.name };
          }),
        );
      })
      .catch(() => alive && setKbs([]));
    return () => {
      alive = false;
    };
  }, [open]);

  if (!open) {
    return (
      <button type="button" className={actionClass} onClick={() => setOpen(true)}>
        <Sparkles size={13} />
        {t("Generate with AI")}
      </button>
    );
  }
  return (
    <div className="space-y-2 rounded-lg bg-[var(--muted)]/40 p-3 text-[12.5px]">
      <textarea
        className="w-full rounded-lg border border-[var(--border)] bg-[var(--background)] p-2"
        rows={2}
        placeholder={t("What should the questions cover?")}
        value={topic}
        onChange={(e) => setTopic(e.target.value)}
      />
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1.5">
          {t("Knowledge base")}
          <select
            className="h-8 max-w-[220px] rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            value={kbRef}
            onChange={(e) => setKbRef(e.target.value)}
          >
            <option value="">{t("None")}</option>
            {kbs.map((kb) => (
              <option key={kb.ref} value={kb.ref}>{kb.name}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5">
          {t("How many")}
          <input
            type="number"
            min={1}
            max={20}
            className="h-8 w-16 rounded-lg border border-[var(--border)] bg-[var(--background)] px-2"
            value={count}
            onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))}
          />
        </label>
        <button
          type="button"
          className={primaryClass}
          disabled={busy || !topic.trim()}
          onClick={() => {
            setBusy(true);
            generateQuestions({ topic: topic.trim(), count, kb_ref: kbRef || null, language: i18n.language?.startsWith("zh") ? "zh" : "en" })
              .then((questions) => {
                onGenerated(questions);
                setTopic("");
              })
              .catch((err) => onError(err instanceof Error ? t(err.message) : String(err)))
              .finally(() => setBusy(false));
          }}
        >
          <Sparkles size={13} />
          {busy ? t("Generating…") : t("Generate")}
        </button>
        <button type="button" className={actionClass} onClick={() => setOpen(false)}>
          {t("Close")}
        </button>
      </div>
      <p className="text-[11.5px] text-[var(--muted-foreground)]">{t("Generated questions are added to the draft; review them before publishing.")}</p>
    </div>
  );
}

function ReportSection({ setId }: { setId: string }) {
  const { t } = useTranslation();
  const [report, setReport] = useState<PracticeReport | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getReport(setId)
      .then((next) => alive && setReport(next))
      .catch((err) => alive && setError(err instanceof Error ? t(err.message) : String(err)));
    return () => {
      alive = false;
    };
  }, [open, setId, t]);

  return (
    <section className={sectionClass}>
      <h3 className={titleClass}>{t("Results")}</h3>
      {!open ? (
        <button type="button" className={actionClass} onClick={() => setOpen(true)}>
          {t("View results")}
        </button>
      ) : error ? (
        <p role="alert" className="text-[12px] text-[var(--destructive)]">{error}</p>
      ) : !report ? (
        <div aria-busy="true" className="h-20 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : report.students.length === 0 ? (
        <p className={hintClass}>{t("Nobody receives this practice set yet.")}</p>
      ) : (
        <div className="space-y-4">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-[12.5px]">
              <thead className="text-left text-[11.5px] text-[var(--muted-foreground)]">
                <tr>
                  <th className="px-2 py-1.5">{t("Student")}</th>
                  <th className="px-2 py-1.5">{t("First score")}</th>
                  <th className="px-2 py-1.5">{t("Best score")}</th>
                  <th className="px-2 py-1.5">{t("Attempts")}</th>
                  <th className="px-2 py-1.5">{t("Minutes")}</th>
                  <th className="px-2 py-1.5">{t("Submitted")}</th>
                </tr>
              </thead>
              <tbody>
                {report.students.map((student) => (
                  <tr key={student.id} className="border-t border-[var(--border)]">
                    <td className="px-2 py-1.5 font-medium">{student.username}</td>
                    <td className="px-2 py-1.5">{student.completed ? `${student.first_score} / ${student.max_score}` : t("Not submitted")}</td>
                    <td className="px-2 py-1.5">{student.completed ? student.best_score : "—"}</td>
                    <td className="px-2 py-1.5">{student.attempts}</td>
                    <td className="px-2 py-1.5">{student.minutes ?? "—"}</td>
                    <td className="px-2 py-1.5">
                      {formatDate(student.submitted_at) || "—"}
                      {student.late && <span className="ml-1 text-amber-600">{t("Late")}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <h4 className="mb-1 text-[12.5px] font-medium">{t("Error rate per question")}</h4>
            <ol className="space-y-1 text-[12.5px]">
              {report.questions.map((question, index) => (
                <li key={question.question_id} className="flex gap-2">
                  <span className="w-12 shrink-0 tabular-nums">
                    {question.error_rate === null ? "—" : `${Math.round(question.error_rate * 100)}%`}
                  </span>
                  <span className="min-w-0 truncate">
                    {index + 1}. {question.question}
                  </span>
                </li>
              ))}
            </ol>
          </div>
          <p className={hintClass}>{t("Results only; students' answers stay in their own question bank.")}</p>
        </div>
      )}
    </section>
  );
}
