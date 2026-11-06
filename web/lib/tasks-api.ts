import { apiFetch, apiUrl } from "./api";
import type { Provenance } from "./provenance";

/** Tasks are system-level: never workspace-scoped (spec 2026-11-03 batch 1). */
const BASE = "/api/tasks";

export type TaskKind = "mastery_path" | "practice_set" | "book" | "note";
/** Tasks have no deadline or opening time (spec 2026-11-05 §1.4). */
export type TaskState = "open" | "completed" | "closed";

export interface TaskSettings {
  ratio?: number;
  chapter_ids?: string[];
  chapter_titles?: string[];
  page_ids?: string[];
}

export interface TaskSummary {
  id: string;
  kind: TaskKind;
  title: string;
  description: string;
  target_id: string;
  target_title: string;
  /** Audit fix B §5: its author archived the practice set or Mastery Path it asks for. */
  target_archived?: boolean;
  settings: TaskSettings;
  status: "draft" | "published" | "archived";
  created_at: number;
  updated_at: number;
  published_at: number | null;
  /** Platform batch 2 §5: the course holding it; empty strings when in none. */
  course_id: string;
  course_title: string;
}

export interface MyTask extends TaskSummary {
  total: number;
  completed: number;
  /** False when what it asks for can no longer be opened by anyone. */
  target_available: boolean;
}

export interface TaskDetail extends TaskSummary {
  students: {
    id: string;
    username: string;
    in_scope: boolean;
    completed: boolean;
    completed_at: number | null;
    self_reported: boolean;
  }[];
}

/** One thing to do, as the student sees it (a task, or a practice set / path no task names). */
export interface AssignedTask {
  key: string;
  task_id: string;
  kind: TaskKind;
  title: string;
  description: string;
  target_id: string;
  target_title: string;
  /** Audit fix B §5: closed because its author archived what it asks for. */
  target_archived?: boolean;
  settings: TaskSettings;
  state: TaskState;
  completed_at: number | null;
  provenance: Provenance;
}

export class TasksApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "TasksApiError";
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const json = typeof init?.body === "string";
  const res = await apiFetch(apiUrl(`${BASE}${path}`), {
    ...init,
    headers: json ? { "Content-Type": "application/json", ...init?.headers } : init?.headers,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new TasksApiError(typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`, res.status);
  }
  return (await res.json()) as T;
}

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});
const id = (value: string) => `/${encodeURIComponent(value)}`;

export const listMyTasks = () => call<{ tasks: MyTask[] }>("/mine").then((r) => r.tasks);
export const createTask = (kind: TaskKind, title: string) =>
  call<{ task: TaskSummary }>("", send("POST", { kind, title })).then((r) => r.task);
export const getTask = (taskId: string) => call<{ task: TaskDetail }>(id(taskId)).then((r) => r.task);
export const updateTask = (
  taskId: string,
  fields: Partial<Pick<TaskSummary, "title" | "description" | "target_id" | "settings">>,
) => call<{ task: TaskSummary }>(id(taskId), send("PUT", fields)).then((r) => r.task);
export const publishTask = (taskId: string) =>
  call<{ task: TaskSummary }>(`${id(taskId)}/publish`, send("POST")).then((r) => r.task);
export const archiveTask = (taskId: string) =>
  call<{ task: TaskSummary }>(`${id(taskId)}/archive`, send("POST")).then((r) => r.task);
export const deleteTask = (taskId: string) => call<{ ok: boolean }>(id(taskId), send("DELETE"));

export const listAssignedTasks = () => call<{ tasks: AssignedTask[] }>("/assigned").then((r) => r.tasks);
export const getAssignedTask = (key: string) =>
  call<{ task: AssignedTask }>(`/assigned/${key.split("/").map(encodeURIComponent).join("/")}`).then((r) => r.task);
export const markTaskDone = (taskId: string) =>
  call<{ task: AssignedTask }>(`/assigned${id(taskId)}/done`, send("POST")).then((r) => r.task);

export interface HomeData {
  /** Platform batch 3 §3: the newest announcements of what one joined. */
  announcements?: import("./catalog-api").HomeAnnouncement[];
  assigned: AssignedTask[];
  history_count: number;
  /** Platform batch 1 §5.3: newly listed in the market (30 days, at most 6). */
  market?: import("./catalog-api").CatalogEntry[];
  mine: { path_id: string; name: string; mastered: number; total: number; updated_at: number }[];
  /** Batch 4 §4: one's own courses still in play. */
  mine_courses?: { id: string; name: string; color: string; updated_at: number }[];
  /** Traceability #6: what one joined from the market, newest first (every role). */
  joined?: JoinedItem[];
}

/** One thing joined from the learning market, and where studying it opens. */
export interface JoinedItem {
  kind: import("./catalog-api").CatalogKind;
  id: string;
  title: string;
  joined_at: number;
  study: import("./catalog-api").StudyTarget;
}

/** The student home in one request (spec 2026-11-03 batch 1 §6). */
export async function getHome(): Promise<HomeData> {
  const res = await apiFetch(apiUrl("/api/home"));
  if (!res.ok) throw new TasksApiError(`Request failed (${res.status})`, res.status);
  return (await res.json()) as HomeData;
}

// Teacher courses (spec 2026-11-03 batch 2 §5): tasks in an order.
export interface CourseSummary {
  id: string;
  title: string;
  status: "draft" | "published" | "archived";
  task_count: number;
  updated_at: number;
}

export interface CourseDetail {
  id: string;
  title: string;
  description: string;
  status: "draft" | "published" | "archived";
  published_at: number | null;
  tasks: TaskSummary[];
}

export interface AssignedCourse {
  id: string;
  title: string;
  description: string;
  provenance: Provenance;
  published_at: number | null;
  /** Audit fix B §2: taken down, or withdrawn by its author; the learner's records stay. */
  unavailable?: "taken_down" | "archived" | null;
  tasks: AssignedTask[];
}

export const listMyCourses = () => call<{ courses: CourseSummary[] }>("/courses/mine").then((r) => r.courses);
export const listAssignedCourses = () => call<{ courses: AssignedCourse[] }>("/courses/assigned").then((r) => r.courses);
export const createCourse = (title: string) =>
  call<{ course: CourseDetail }>("/courses", send("POST", { title })).then((r) => r.course);
export const getCourse = (courseId: string) => call<{ course: CourseDetail }>(`/courses${id(courseId)}`).then((r) => r.course);
export const updateCourse = (courseId: string, fields: { title?: string; description?: string; task_ids?: string[] }) =>
  call<{ course: CourseDetail }>(`/courses${id(courseId)}`, send("PUT", fields)).then((r) => r.course);
export const addCourseTask = (courseId: string, kind: TaskKind, title: string) =>
  call<{ course: CourseDetail }>(`/courses${id(courseId)}/tasks`, send("POST", { kind, title })).then((r) => r.course);
/** Follow-ups §3: one's published practice set becomes the course's next task. */
export const addCoursePracticeSet = (courseId: string, setId: string) =>
  call<{ course: CourseDetail }>(`/courses${id(courseId)}/practice-sets`, send("POST", { set_id: setId })).then((r) => r.course);
export const publishCourse = (courseId: string) =>
  call<{ course: CourseDetail }>(`/courses${id(courseId)}/publish`, send("POST")).then((r) => r.course);
export const archiveCourse = (courseId: string) => call(`/courses${id(courseId)}/archive`, send("POST"));
export const deleteCourse = (courseId: string) => call<{ ok: boolean }>(`/courses${id(courseId)}`, send("DELETE"));
/** Traceability #5: an administrator gives a course they hold to a teacher. */
export const handOverCourse = (courseId: string, learnerId: string) =>
  call<{ course: { id: string; owner_user_id: string } }>(`/courses${id(courseId)}/hand-over`, send("POST", { teacher_id: learnerId }));
/** The same for a task that is in no course. */
export const handOverTask = (taskId: string, learnerId: string) =>
  call<{ task: { id: string; owner_user_id: string } }>(`${id(taskId)}/hand-over`, send("POST", { teacher_id: learnerId }));

