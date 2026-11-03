import { apiFetch, apiUrl } from "./api";
import type { Provenance } from "./provenance";
import type { ScopeUpdate } from "./publish-api";

/** Tasks are system-level: never workspace-scoped (spec 2026-11-03 batch 1). */
const BASE = "/api/tasks";

export type TaskKind = "mastery_path" | "practice_set" | "book" | "homework" | "note";
/** ``not_open``: published, but its opening time has not come (spec 2026-11-03 batch 2 §6). */
export type TaskState = "not_open" | "open" | "overdue" | "completed" | "closed";

export interface TaskSettings {
  ratio?: number;
  chapter_ids?: string[];
  chapter_titles?: string[];
  page_ids?: string[];
  max_score?: number;
  rubric?: string;
}

export interface TaskSummary {
  id: string;
  kind: TaskKind;
  title: string;
  description: string;
  target_id: string;
  target_title: string;
  settings: TaskSettings;
  due_at: number | null;
  open_at: number | null;
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
  to_grade: number;
  /** False when what it asks for can no longer be opened by anyone. */
  target_available: boolean;
}

export interface Submission {
  text: string;
  files: { name: string; size: number }[];
  attempt: number;
  submitted_at: number;
  late: boolean;
  status: "submitted" | "returned" | "graded";
  score: number | null;
  comment: string;
  return_reason: string;
  graded_at: number | null;
  /** R6 / batch 4 §1: the class average once 3 or more are graded; never a ranking. */
  class_average?: number | null;
  /** Teacher only. */
  ai_score?: number | null;
  ai_reason?: string;
}

export interface TaskDetail extends TaskSummary {
  students: {
    id: string;
    username: string;
    in_scope: boolean;
    completed: boolean;
    completed_at: number | null;
    late: boolean;
    self_reported: boolean;
    submission: Submission | null;
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
  settings: TaskSettings;
  due_at: number | null;
  open_at?: number | null;
  state: TaskState;
  completed_at: number | null;
  late: boolean;
  provenance: Provenance;
  submission?: Submission | null;
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
  fields: Partial<Pick<TaskSummary, "title" | "description" | "target_id" | "settings" | "due_at" | "open_at">>,
) => call<{ task: TaskSummary }>(id(taskId), send("PUT", fields)).then((r) => r.task);
/** ``scope``: who receives a first publication (spec 2026-11-02 §4.1). */
export const publishTask = (taskId: string, scope?: ScopeUpdate) =>
  call<{ task: TaskSummary }>(`${id(taskId)}/publish`, send("POST", scope ? { scope } : {})).then((r) => r.task);
export const archiveTask = (taskId: string) =>
  call<{ task: TaskSummary }>(`${id(taskId)}/archive`, send("POST")).then((r) => r.task);
export const deleteTask = (taskId: string) => call<{ ok: boolean }>(id(taskId), send("DELETE"));
export const gradeHomework = (taskId: string, studentId: string, score: number, comment: string) =>
  call<{ task: TaskDetail }>(`${id(taskId)}/students${id(studentId)}/grade`, send("POST", { score, comment })).then(
    (r) => r.task,
  );
export const sendBackHomework = (taskId: string, studentId: string, reason: string) =>
  call<{ task: TaskDetail }>(`${id(taskId)}/students${id(studentId)}/send-back`, send("POST", { reason })).then(
    (r) => r.task,
  );
export const homeworkFileUrl = (taskId: string, studentId: string, name: string) =>
  apiUrl(`${BASE}${id(taskId)}/students${id(studentId)}/files${id(name)}`);

export const listAssignedTasks = () => call<{ tasks: AssignedTask[] }>("/assigned").then((r) => r.tasks);
export const getAssignedTask = (key: string) =>
  call<{ task: AssignedTask }>(`/assigned/${key.split("/").map(encodeURIComponent).join("/")}`).then((r) => r.task);
export const markTaskDone = (taskId: string) =>
  call<{ task: AssignedTask }>(`/assigned${id(taskId)}/done`, send("POST")).then((r) => r.task);
export const submitHomework = (taskId: string, text: string, files: File[]) => {
  const form = new FormData();
  form.append("text", text);
  for (const file of files) form.append("files", file);
  return call<{ task: AssignedTask }>(`/assigned${id(taskId)}/submit`, { method: "POST", body: form }).then(
    (r) => r.task,
  );
};

export interface HomeData {
  announcements?: import("./classes-api").Announcement[];
  assigned: AssignedTask[];
  history_count: number;
  /** Platform batch 1 §5.3: newly listed in the market (30 days, at most 6). */
  market?: import("./catalog-api").CatalogEntry[];
  mine: { path_id: string; name: string; mastered: number; total: number; updated_at: number }[];
  /** Batch 4 §4: one's own courses still in play. */
  mine_courses?: { id: string; name: string; color: string; updated_at: number }[];
  can_create_path: boolean;
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
export const publishCourse = (courseId: string, scope?: ScopeUpdate) =>
  call<{ course: CourseDetail }>(`/courses${id(courseId)}/publish`, send("POST", scope ? { scope } : {})).then((r) => r.course);
export const archiveCourse = (courseId: string) => call(`/courses${id(courseId)}/archive`, send("POST"));
export const deleteCourse = (courseId: string) => call<{ ok: boolean }>(`/courses${id(courseId)}`, send("DELETE"));

// The calendar (spec 2026-11-03 batch 2 §7).
export interface CalendarEvent {
  key: string;
  title: string;
  kind: TaskKind;
  state: string;
  type: "open" | "due";
  at: number;
}
export const listCalendar = () => call<{ events: CalendarEvent[] }>("/calendar").then((r) => r.events);
