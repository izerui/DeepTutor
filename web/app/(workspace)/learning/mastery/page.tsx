"use client";

import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  CourseScopeChip,
  useCourseScope,
} from "@/components/courses/CourseScope";
import { CreateTopicWizard } from "@/components/space/learning/CreateTopicWizard";
import { PublishTemplateDialog } from "@/components/space/learning/PublishTemplateDialog";
import { TemplateAssignmentsDialog } from "@/components/space/learning/TemplateAssignmentsDialog";
import { unlistedTemplates } from "@/components/space/learning/teaching";
import type { Translate } from "@/components/space/learning/format";
import { topicDisplayName } from "@/components/space/learning/format";
import { TopicAtlas } from "@/components/space/learning/TopicAtlas";
import { activeWorkspaceId } from "@/lib/workspace-scope";
import { learningLibrary } from "@/lib/learning-library";
import { useLearningCreation, requestedLearningCreation } from "@/components/learning/LibraryWorkspace";
import { type MasteryTopic } from "@/lib/learning-api";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { listTeacherTemplates, type TeacherTemplate } from "@/lib/teaching-api";
import {
  MASTERY_OPENING_SCOPE,
  masteryOpeningMessage,
  masterySessionRoute,
} from "@/lib/mastery-mode";
import { setPendingPrompt } from "@/lib/pending-prompt";

/**
 * Resolve the freshest version of a template the user is managing.
 *
 * `managing` is the snapshot taken when the dialog opened; `templates` is the
 * live list that `loadTemplates()` keeps current. Picking from the list means
 * the dialog sees status changes (archive, new revision) without the parent
 * having to push them through a second piece of state.
 */
export function currentTemplate<T extends { id: string }>(
  templates: readonly T[],
  managing: T,
): T {
  return templates.find((t) => t.id === managing.id) ?? managing;
}

function MasteryPathRoute() {
  const router = useRouter();
  const { t } = useTranslation();
  const [topics, setTopics] = useState<MasteryTopic[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(requestedLearningCreation);
  const wizardTriggerRef = useRef<HTMLElement | null>(null);
  // Present when opened from a course page or a Course Study hand-off. It both
  // narrows the atlas to that course's paths and adopts whatever is built here.
  const scope = useCourseScope();
  const creation = useLearningCreation(() => setWizardOpen(true));
  const { isTeacher, allowPersonalMastery } = useAuthStatus();
  const [templates, setTemplates] = useState<TeacherTemplate[]>([]);
  const [publishing, setPublishing] = useState<{
    topic: MasteryTopic;
    template?: TeacherTemplate;
  } | null>(null);
  const [managing, setManaging] = useState<TeacherTemplate | null>(null);
  const teacherTriggerRef = useRef<HTMLElement | null>(null);
  const templatesRequest = useRef(0);

  const loadTemplates = useCallback(async () => {
    if (!isTeacher) return;
    // Overlapping reloads (after each assignment change) settle on the newest.
    const request = ++templatesRequest.current;
    try {
      const next = await listTeacherTemplates();
      if (request === templatesRequest.current) setTemplates(next);
    } catch {
      // The atlas stays usable; the card actions simply show "publish".
      if (request === templatesRequest.current) setTemplates([]);
    }
  }, [isTeacher]);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const loadTopics = useCallback(async () => {
    setError(null);
    try {
      const result = await learningLibrary<MasteryTopic>("mastery");
      setTopics(result.items);
      if (result.unavailable_workspaces.length) setError(t("Some workspaces could not be loaded. Available content is shown."));
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("The atlas could not be loaded."),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadTopics();
  }, [loadTopics]);

  // A course that references no path yet scopes the atlas to nothing, and the
  // empty state then invites building the first one — which is the move that
  // was previously a dead end.
  const scopedTopics = useMemo(() => {
    if (!scope) return topics;
    const allowed = new Set(scope.refIds("mastery_path"));
    return topics.filter((topic) => allowed.has(topic.path_id) && (topic.content_workspace_id ?? "") === activeWorkspaceId());
  }, [scope, topics]);

  // Publishing is offered on the owner's own cards only: an admin sees other
  // teachers' paths in a separate list, where the server lets them manage
  // students and archive but never publish (D17).
  const ownTemplates = useMemo(
    () => templates.filter((template) => template.is_owner),
    [templates],
  );
  const otherTemplates = useMemo(
    () => (scope ? [] : unlistedTemplates(templates, topics)),
    [scope, templates, topics],
  );

  return (
    <>
      {allowPersonalMastery && creation.dialog}
      <TopicAtlas
        topics={scopedTopics}
        loading={loading}
        error={error}
        scopeChip={scope ? <CourseScopeChip scope={scope} /> : null}
        // A student whose policy forbids personal paths gets no create
        // control at all, rather than one that does nothing (R12).
        onCreate={
          allowPersonalMastery
            ? (trigger) => {
                wizardTriggerRef.current = trigger;
                creation.begin();
              }
            : undefined
        }
        onRetry={() => {
          setLoading(true);
          void loadTopics();
        }}
        teacherTemplates={isTeacher ? ownTemplates : undefined}
        unlistedTemplates={isTeacher ? otherTemplates : undefined}
        onPublish={(topic, template, trigger) => {
          teacherTriggerRef.current = trigger;
          setPublishing({ topic, template });
        }}
        onManage={(template, trigger) => {
          teacherTriggerRef.current = trigger;
          setManaging(template);
        }}
      />
      {/* `?create=1` and create shortcuts cannot open it around the policy
          either; the server refuses the creation regardless. */}
      {wizardOpen && allowPersonalMastery && (
        <CreateTopicWizard
          returnFocusRef={wizardTriggerRef}
          onClose={() => setWizardOpen(false)}
          onCreated={async (topic) => {
            setWizardOpen(false);
            await scope?.attach(
              "mastery_path",
              topic.path_id,
              topicDisplayName(topic, t as Translate),
            );
            // Straight into the goal's outline session: a goal is created
            // without an outline, and that kind of session is the one that
            // designs it — it cannot examine the learner, and it opens by
            // reading their materials rather than waiting to be prompted.
            // The button they pressed is the request; the conversation opens
            // by sending it rather than by asking them to phrase it again.
            setPendingPrompt(
              masteryOpeningMessage("outline", t),
              MASTERY_OPENING_SCOPE,
            );
            router.push(masterySessionRoute(topic.path_id, "outline"));
          }}
        />
      )}
      {publishing && (
        <PublishTemplateDialog
          topic={publishing.topic}
          template={publishing.template}
          returnFocusRef={teacherTriggerRef}
          onClose={() => {
            setPublishing(null);
            // Opening the dialog may have registered the path as a template.
            void loadTemplates();
          }}
          onPublished={() => {
            setPublishing(null);
            void loadTemplates();
          }}
        />
      )}
      {managing && (
        <TemplateAssignmentsDialog
          template={currentTemplate(templates, managing)}
          returnFocusRef={teacherTriggerRef}
          onClose={() => setManaging(null)}
          onChanged={() => void loadTemplates()}
        />
      )}
    </>
  );
}

export default function MasteryPathPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--muted-foreground)]" />
        </div>
      }
    >
      <MasteryPathRoute />
    </Suspense>
  );
}
