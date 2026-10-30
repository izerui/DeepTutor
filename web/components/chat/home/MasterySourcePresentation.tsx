"use client";

import { useTranslation } from "react-i18next";
import type { StreamEvent } from "@/features/chat/model/protocol";
import { apiUrl } from "@/lib/api";
import ImagePreview from "@/components/chat/preview/previewers/ImagePreview";
import { useBinarySource } from "@/components/chat/preview/previewers/useBinarySource";
import { useMasteryTemplate } from "@/components/space/learning/mastery-template-context";
import { withMasteryTemplate } from "@/lib/mastery-template-scope";
import { COURSE_UNAVAILABLE_KEY, AUTHOR_PATH_UNAVAILABLE_CODE } from "@/lib/author-path-messages";
import { presentedPagesFromSources, type PresentedPage } from "@/lib/mastery-source-pages";

type Page = PresentedPage;

function presentedPages(events: StreamEvent[], placed: ReadonlySet<string>): Page[] {
  const pages = new Map<string, Page>();
  for (const event of events) {
    if (event.type !== "sources" || !["chat", "mastery_path"].includes(event.source)) continue;
    for (const page of presentedPagesFromSources(event.metadata.sources)) {
      // A page already shown at its tool call is not stacked again below.
      if (!placed.has(page.preview_url)) pages.set(page.preview_url, page);
    }
  }
  return [...pages.values()].slice(0, 12);
}

function SourcePage({ page }: { page: Page }) {
  const { t } = useTranslation();
  // R9: inside an open author path's Study page the original is read in the
  // path's context; elsewhere the context is "" and the URL is unchanged.
  const templateId = useMasteryTemplate();
  const state = useBinarySource(apiUrl(withMasteryTemplate(page.preview_url, templateId)), "image/png");
  const url = state.kind === "ready" ? state.objectUrl : null;
  // Only the server's code says the course closed; any other refusal (even a
  // 403 in a path's context) is a failed image like anywhere else.
  const closed =
    state.kind === "error" && state.status === 403 && state.code === AUTHOR_PATH_UNAVAILABLE_CODE;
  return <figure className="my-3 rounded-lg border border-[var(--border)] p-2">
    <figcaption className="mb-2 break-words text-xs text-[var(--muted-foreground)]">{page.title}</figcaption>
    {url ? <div className="h-96 w-full"><ImagePreview url={url} filename={page.title} /></div>
      : <p role="status" className="text-sm">{closed ? t(COURSE_UNAVAILABLE_KEY) : state.kind === "error" ? t("Failed to load image.") : t("Loading...")}</p>}
  </figure>;
}

/** Render publication receipts; publication does not prove that a learner viewed a page. */
export function MasterySourcePresentation({ events, placed }: {
  events: StreamEvent[];
  /** Preview URLs already rendered where their tool call happened. */
  placed?: ReadonlySet<string>;
}) {
  return <>{presentedPages(events, placed ?? NONE).map(page => <SourcePage key={page.preview_url} page={page} />)}</>;
}

const NONE: ReadonlySet<string> = new Set();

/** The pages one tool call presented, shown where that call happened. */
export function MasterySourcePages({ pages }: { pages: PresentedPage[] }) {
  return <>{pages.map(page => <SourcePage key={page.preview_url} page={page} />)}</>;
}
