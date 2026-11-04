"use client";

import { ArrowDown, ArrowLeft, ArrowUp } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass, inputClass, primaryClass } from "@/components/common/form-styles";
import {
  addTaxonomyNode,
  deleteTaxonomyNode,
  getAdminTags,
  getAdminTaxonomy,
  mergeTags,
  moveTaxonomyNode,
  unmergeTag,
  updateTaxonomyNode,
  type AdminTags,
  type AdminTaxonomy,
  type AdminTaxonomyNode,
} from "@/lib/catalog-api";

import { AdminNav } from "./AdminNav";
import { catalogError, formatDay } from "./catalog-format";
import { MAX_TAG_LENGTH } from "./TagInput";
import { pickName, resetTaxonomyCache } from "./useTaxonomy";

const NAME_MAX = 40;
const small = "rounded-lg border border-[var(--border)] px-2 py-1 text-[12px] hover:bg-[var(--muted)] disabled:opacity-40";

type Run = (work: () => Promise<AdminTaxonomy>) => Promise<boolean>;

/** One entry: both names, hidden, its place among its siblings, and how many listings use it. */
function NodeRow({
  node,
  index,
  count,
  run,
  depth,
}: {
  node: AdminTaxonomyNode;
  index: number;
  count: number;
  run: Run;
  depth: 0 | 1 | 2;
}) {
  const { t, i18n } = useTranslation();
  const [zh, setZh] = useState(node.names.zh);
  const [en, setEn] = useState(node.names.en);
  const [seen, setSeen] = useState(node.names);
  if (seen !== node.names) {
    setSeen(node.names);
    setZh(node.names.zh);
    setEn(node.names.en);
  }
  const label = pickName(node.names, i18n?.language) || node.id;
  const changed = zh.trim() !== node.names.zh || en.trim() !== node.names.en;
  return (
    <div
      className={`flex flex-wrap items-center gap-2 rounded-lg border border-[var(--border)] px-2 py-1.5 text-[12.5px] ${node.hidden ? "opacity-60" : ""}`}
      style={{ marginLeft: depth * 20 }}
      data-testid={`node-${node.id}`}
    >
      <input className={`${inputClass} w-36`} value={zh} maxLength={NAME_MAX}
        aria-label={t("Chinese name of {{name}}", { name: label })} onChange={(e) => setZh(e.target.value)} />
      <input className={`${inputClass} w-40`} value={en} maxLength={NAME_MAX}
        aria-label={t("English name of {{name}}", { name: label })} onChange={(e) => setEn(e.target.value)} />
      <button type="button" className={small} disabled={!changed}
        onClick={() => void run(() => updateTaxonomyNode(node.id, { name_zh: zh.trim(), name_en: en.trim() }))}>
        {t("Save")}
      </button>
      <label className="inline-flex items-center gap-1">
        <input type="checkbox" checked={node.hidden}
          aria-label={t("Hide {{name}}", { name: label })}
          onChange={(e) => void run(() => updateTaxonomyNode(node.id, { hidden: e.target.checked }))} />
        {t("Hidden")}
      </label>
      <button type="button" className={small} disabled={index === 0}
        aria-label={t("Move {{name}} up", { name: label })}
        onClick={() => void run(() => moveTaxonomyNode(node.id, "up"))}>
        <ArrowUp size={12} />
      </button>
      <button type="button" className={small} disabled={index === count - 1}
        aria-label={t("Move {{name}} down", { name: label })}
        onClick={() => void run(() => moveTaxonomyNode(node.id, "down"))}>
        <ArrowDown size={12} />
      </button>
      <span className="text-[11.5px] text-[var(--muted-foreground)]">{t("In use: {{count}}", { count: node.in_use })}</span>
      {node.custom ? (
        <button type="button" className={`${small} text-red-600`}
          aria-label={t("Delete {{name}}", { name: label })}
          onClick={() => {
            if (window.confirm(t("Delete “{{name}}”?", { name: label }))) void run(() => deleteTaxonomyNode(node.id));
          }}>
          {t("Delete")}
        </button>
      ) : (
        <span className="text-[11px] text-[var(--muted-foreground)]">{t("Built in")}</span>
      )}
    </div>
  );
}

/** "Add stage", "Add subject" or "Add sub-subject": a Chinese and an English name. */
function AddNode({ parent, label, run, depth }: { parent: string; label: string; run: Run; depth: 0 | 1 | 2 }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [zh, setZh] = useState("");
  const [en, setEn] = useState("");
  if (!open) {
    return (
      <button type="button" className="text-[12px] text-[var(--primary)] hover:underline" style={{ marginLeft: depth * 20 }}
        onClick={() => setOpen(true)}>
        + {label}
      </button>
    );
  }
  return (
    <form
      className="flex flex-wrap items-center gap-2 text-[12.5px]"
      style={{ marginLeft: depth * 20 }}
      aria-label={label}
      onSubmit={(e) => {
        e.preventDefault();
        void run(() => addTaxonomyNode(parent, zh.trim(), en.trim())).then((ok) => {
          if (!ok) return;
          setZh("");
          setEn("");
          setOpen(false);
        });
      }}
    >
      <input className={`${inputClass} w-36`} value={zh} maxLength={NAME_MAX} placeholder={t("Chinese name")}
        aria-label={t("Chinese name")} onChange={(e) => setZh(e.target.value)} />
      <input className={`${inputClass} w-40`} value={en} maxLength={NAME_MAX} placeholder={t("English name")}
        aria-label={t("English name")} onChange={(e) => setEn(e.target.value)} />
      <button type="submit" className={primaryClass} disabled={!zh.trim() && !en.trim()}>{t("Add")}</button>
      <button type="button" className={actionClass} onClick={() => setOpen(false)}>{t("Cancel")}</button>
    </form>
  );
}

/** Platform batch 2 §8.1: rename, hide, reorder, add; delete only one's own unused entries. */
export function TaxonomyAdmin() {
  const { t } = useTranslation();
  const [tree, setTree] = useState<AdminTaxonomy | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getAdminTaxonomy()
      .then((next) => alive && setTree(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [t]);

  const run: Run = async (work) => {
    setBusy(true);
    setError("");
    try {
      setTree(await work());
      // The market and the pickers read the classification again.
      resetTaxonomyCache();
      return true;
    } catch (err) {
      setError(catalogError(err, t));
      return false;
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="catalog-taxonomy" className="space-y-3">
      <div>
        <h2 id="catalog-taxonomy" className="text-[14px] font-semibold">{t("Classification")}</h2>
        <p className="text-[12px] text-[var(--muted-foreground)]">
          {t("Hidden entries are not offered in the market filters or when listing; content that uses them keeps them. Built-in entries can only be hidden.")}
        </p>
      </div>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {!tree ? (
        !error && <div aria-busy="true" className="h-40 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : (
        <fieldset disabled={busy} className="space-y-3">
          {tree.stages.map((stage, si) => (
            <div key={stage.id} className="space-y-1.5 rounded-xl border border-[var(--border)] p-2">
              <NodeRow node={stage} index={si} count={tree.stages.length} run={run} depth={0} />
              {stage.subjects.map((subject, ji) => (
                <div key={subject.id} className="space-y-1.5">
                  <NodeRow node={subject} index={ji} count={stage.subjects.length} run={run} depth={1} />
                  {subject.children.map((child, ci) => (
                    <NodeRow key={child.id} node={child} index={ci} count={subject.children.length} run={run} depth={2} />
                  ))}
                  <AddNode parent={subject.id} label={t("Add sub-subject")} run={run} depth={2} />
                </div>
              ))}
              <AddNode parent={stage.id} label={t("Add subject")} run={run} depth={1} />
            </div>
          ))}
          <AddNode parent="" label={t("Add stage")} run={run} depth={0} />
        </fieldset>
      )}
    </section>
  );
}

/** Platform batch 2 §8.2: every tag in the market, merging one into another, and undoing a merge. */
export function TagAdmin() {
  const { t } = useTranslation();
  const [data, setData] = useState<AdminTags | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getAdminTags()
      .then((next) => alive && setData(next))
      .catch((err) => alive && setError(catalogError(err, t)));
    return () => {
      alive = false;
    };
  }, [t]);

  const run = async (work: () => Promise<AdminTags>) => {
    setBusy(true);
    setError("");
    try {
      setData(await work());
      resetTaxonomyCache();
      return true;
    } catch (err) {
      setError(catalogError(err, t));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const ready = from.trim() && to.trim() && from.trim().toLocaleLowerCase() !== to.trim().toLocaleLowerCase();
  return (
    <section aria-labelledby="catalog-tags" className="space-y-3">
      <h2 id="catalog-tags" className="text-[14px] font-semibold">{t("Tags")}</h2>
      {error && <p role="alert" className="text-[12px] text-red-600">{error}</p>}
      {!data ? (
        !error && <div aria-busy="true" className="h-24 animate-pulse rounded-xl bg-[var(--muted)]" />
      ) : (
        <fieldset disabled={busy} className="space-y-4">
          {data.tags.length === 0 ? (
            <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No listing has tags yet.")}</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5" aria-label={t("Tags in use")}>
              {data.tags.map((row) => (
                <li key={row.tag}>
                  <button type="button" className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[12px] hover:bg-[var(--muted)]/70"
                    onClick={() => (from ? setTo(row.tag) : setFrom(row.tag))}>
                    #{row.tag} <span className="text-[var(--muted-foreground)]">{row.count}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <datalist id="catalog-tag-options">
            {data.tags.map((row) => <option key={row.tag} value={row.tag} />)}
          </datalist>
          <form
            className="flex flex-wrap items-center gap-2 text-[12.5px]"
            aria-label={t("Merge tags")}
            onSubmit={(e) => {
              e.preventDefault();
              if (!ready) return;
              void run(() => mergeTags(from.trim(), to.trim())).then((ok) => {
                if (!ok) return;
                setFrom("");
                setTo("");
              });
            }}
          >
            <span>{t("Merge")}</span>
            <input className={`${inputClass} w-40`} list="catalog-tag-options" value={from} maxLength={MAX_TAG_LENGTH}
              aria-label={t("Tag to merge")} onChange={(e) => setFrom(e.target.value)} />
            <span>{t("into")}</span>
            <input className={`${inputClass} w-40`} list="catalog-tag-options" value={to} maxLength={MAX_TAG_LENGTH}
              aria-label={t("Tag to keep")} onChange={(e) => setTo(e.target.value)} />
            <button type="submit" className={primaryClass} disabled={!ready}>{t("Merge")}</button>
          </form>
          <p className="text-[11.5px] text-[var(--muted-foreground)]">
            {t("Every listing's tag is replaced, and the tag is replaced whenever someone enters it later.")}
          </p>
          <div className="space-y-1.5">
            <h3 className="text-[13px] font-medium">{t("Merges")}</h3>
            {data.merges.length === 0 ? (
              <p className="text-[12.5px] text-[var(--muted-foreground)]">{t("No merges yet.")}</p>
            ) : (
              <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)]">
                {data.merges.map((m) => (
                  <li key={m.from} className="flex flex-wrap items-center gap-2 p-2 text-[12.5px]">
                    <span>#{m.from} → #{m.to}</span>
                    <span className="text-[11px] text-[var(--muted-foreground)]">{formatDay(m.at)}</span>
                    <button type="button" className={`${small} ml-auto`}
                      aria-label={t("Undo merging {{tag}}", { tag: m.from })}
                      onClick={() => void run(() => unmergeTag(m.from))}>
                      {t("Undo")}
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[11.5px] text-[var(--muted-foreground)]">
              {t("Undoing stops future replacement; tags already changed stay.")}
            </p>
          </div>
        </fieldset>
      )}
    </section>
  );
}

/** ``/admin/catalog`` (platform batch 2 §8): administrators only. */
export default function CatalogAdmin() {
  const { t } = useTranslation();
  return (
    <div className="h-screen overflow-y-auto bg-[var(--background)] px-4 py-10 [scrollbar-gutter:stable]">
      <div className="mx-auto max-w-5xl space-y-8">
        <div>
          <Link href="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
            <ArrowLeft size={16} />
            {t("Back")}
          </Link>
          <AdminNav />
          <h1 className="font-serif text-xl font-semibold">{t("Classification and tags")}</h1>
        </div>
        <TaxonomyAdmin />
        <TagAdmin />
      </div>
    </div>
  );
}
