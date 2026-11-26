"use client";

import { practiceRoute, readingCollectionRoute } from "@/lib/learning-routes";
import {
  DEFAULT_MASTERY_MODE,
  MASTERY_OPENING_SCOPE,
  masteryOpeningMessage,
  masterySessionRoute,
} from "@/lib/mastery-mode";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Signpost } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { CourseNextStepData, CourseState } from "@/lib/courses-api";
import { setPendingPrompt } from "@/lib/pending-prompt";

/**
 * What this course is waiting for.
 *
 * The order is not decided here. The server ranks every candidate once
 * (``deeptutor/learning/next_steps.py``) and Course Study is given the same
 * list, so the page and the tutor can never disagree about what comes first.
 * This component only turns the top step into words and a destination.
 *
 * Each action carries its opening line through `setPendingPrompt`, so taking it
 * lands in the destination already asking the right question — the same
 * hand-off contract the tutor's cards use.
 */

interface NextStep {
  /** What the learner is being told, in one line. */
  headline: string;
  /** The evidence behind it. */
  detail: string;
  action: { label: string; href: string; scope: string; prompt: string } | null;
}

type Translate = (key: string, options?: Record<string, unknown>) => string;

function present(step: CourseNextStepData | undefined, t: Translate, courseId: string): NextStep {
  const courseChat = `/chat?course=${encodeURIComponent(courseId)}&capability=course_study`;
  switch (step?.kind) {
    case "practice_review":
      return {
        headline: t("Review what is due today"),
        detail: t("{{count}} reviews due", { count: step.count }),
        action: {
          label: t("Start review"),
          href: practiceRoute(new URLSearchParams({ course: courseId })),
          scope: "",
          prompt: "",
        },
      };
    case "path_review":
      return {
        headline: step.name,
        detail: t("{{count}} reviews due", { count: step.count }),
        action: {
          label: t("Start review"),
          href: masterySessionRoute(step.path_id, "review", courseId),
          scope: MASTERY_OPENING_SCOPE,
          prompt: masteryOpeningMessage("review", t, { dueTitles: [] }),
        },
      };
    case "open_mistakes":
      return {
        headline: step.name
          ? t("{{name}} is where you are losing marks", { name: step.name })
          : t("Work through your open mistakes"),
        detail: step.name
          ? t("{{count}} questions wrong in this area", { count: step.count })
          : t("{{count}} questions still wrong", { count: step.count }),
        action: {
          // Chat, not the question-bank list: this is a request to be taught, and
          // the list has no composer to receive it.
          label: t("Work through them"),
          href: courseChat,
          scope: "chat",
          prompt: step.name
            ? t("Take me through my {{name}} mistakes one at a time.", { name: step.name })
            : t("Take me through my open mistakes one at a time."),
        },
      };
    case "continue_path":
      return {
        headline: t("{{name}}: {{done}} of {{total}} objectives cleared", {
          name: step.name,
          done: step.done,
          total: step.total,
        }),
        detail: step.weak_point || t("Pick up where the path left off"),
        action: {
          label: t("Keep going"),
          // The course travels with the session, so work done there counts here.
          href: masterySessionRoute(step.path_id, DEFAULT_MASTERY_MODE, courseId),
          scope: "mastery_path",
          prompt: t("Continue this path from where I stopped."),
        },
      };
    case "syllabus_next":
      return {
        headline: t("Next in the syllabus: {{title}}", { title: step.unit.title }),
        detail: t("{{covered}} of {{total}} units covered", {
          covered: step.covered,
          total: step.total,
        }),
        action: {
          label: t("Study this unit"),
          href: courseChat,
          scope: "chat",
          prompt: t(
            "Teach me the syllabus unit {{title}} from this subject's material, then check that I understood it.",
            { title: step.unit.title },
          ),
        },
      };
    case "continue_reading":
      return {
        headline: t("{{title}} is open and waiting", { title: step.title }),
        detail: t("{{count}} materials in this reading workspace", { count: step.materials }),
        action: {
          label: t("Read on"),
          href: readingCollectionRoute(step.workspace_id),
          scope: "immersive_reading",
          prompt: t("Summarise where I left off, then continue from there."),
        },
      };
    case "plan":
      return {
        headline: t("The material is in place"),
        detail: t(
          "Nothing has been built from it yet — a mastery path turns it into something you can practise.",
        ),
        action: {
          label: t("Plan this subject"),
          href: courseChat,
          scope: "chat",
          prompt: t("Look at what this subject has and plan what I should do first."),
        },
      };
    default:
      return {
        headline: t("This subject has nothing to work with yet"),
        detail: t(
          "Attach its textbook or knowledge base and DeepTutor can start planning from the material itself.",
        ),
        action: null,
      };
  }
}

export default function CourseNextStep({
  state,
  courseId,
}: {
  state: CourseState | null;
  courseId: string;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const step = present(state?.next_steps?.[0], t, courseId);
  // Client navigation to these surfaces loads their chunk first, which is
  // slow enough to feel like a dead click without this.
  const [going, setGoing] = useState(false);

  const take = () => {
    if (!step.action || going) return;
    setGoing(true);
    if (step.action.prompt) setPendingPrompt(step.action.prompt, step.action.scope);
    router.push(step.action.href);
  };

  return (
    <section className="rounded-2xl border border-[var(--border)] bg-[var(--secondary)]/40 p-4">
      <div className="flex items-start gap-3">
        <Signpost
          size={15}
          strokeWidth={1.8}
          className="mt-0.5 shrink-0 text-[var(--muted-foreground)]"
        />
        <div className="min-w-0 flex-1">
          <p className="font-serif text-[15px] font-semibold leading-snug text-[var(--foreground)]">
            {step.headline}
          </p>
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--muted-foreground)]">
            {step.detail}
          </p>
        </div>
        {step.action ? (
          <button
            type="button"
            onClick={take}
            disabled={going}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 text-[12px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/50 disabled:opacity-60"
          >
            {going ? t("Opening") : step.action.label}
            <ArrowRight size={13} />
          </button>
        ) : null}
      </div>
    </section>
  );
}
