import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

// Platform follow-ups: reviews and favorites, grades, remedial into the course,
// grading the hand-in one read, provenance wording, subjects by stage, CSV downloads.
const { api, tasks, practice, router, search, listingStatus, t } = vi.hoisted(() => ({
  api: {
    getTaxonomy: vi.fn(),
    searchMarket: vi.fn(),
    listReviews: vi.fn(),
    saveMyReview: vi.fn(),
    deleteMyReview: vi.fn(),
    deleteReview: vi.fn(),
    replyToReview: vi.fn(),
    setFavorite: vi.fn(),
  },
  tasks: { gradeHomework: vi.fn(), sendBackHomework: vi.fn(), getTask: vi.fn(), getCourse: vi.fn(), addCoursePracticeSet: vi.fn() },
  practice: { getSet: vi.fn(), getReport: vi.fn() },
  router: { replace: vi.fn(), push: vi.fn() },
  search: { value: "" },
  listingStatus: { value: "" },
  t: (key: string, values?: Record<string, unknown>) =>
    key.replace(/\{\{(\w+)\}\}/g, (_: string, name: string) => String(values?.[name] ?? "")),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t, i18n: { language: "en" } }) }));
vi.mock("@/hooks/useAuthStatus", () => ({
  useAuthStatus: () => ({ isAdmin: false, isTeacher: true, enabled: true, loading: false }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/market",
  useSearchParams: () => new URLSearchParams(search.value),
}));
vi.mock("@/lib/catalog-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/catalog-api")>("@/lib/catalog-api")),
  ...api,
}));
vi.mock("@/lib/tasks-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/tasks-api")>("@/lib/tasks-api")),
  ...tasks,
}));
vi.mock("@/lib/practice-sets-api", async () => ({
  ...(await vi.importActual<typeof import("@/lib/practice-sets-api")>("@/lib/practice-sets-api")),
  ...practice,
}));
vi.mock("@/components/catalog/ListingPanel", async () => {
  const { useEffect } = await vi.importActual<typeof import("react")>("react");
  return {
    ListingPanel: ({ onStatus }: { onStatus?: (status: string) => void }) => {
      useEffect(() => {
        if (listingStatus.value) onStatus?.(listingStatus.value);
      }, [onStatus]);
      return null;
    },
  };
});
vi.mock("@/components/chat/QuestionBankPicker", () => ({ default: () => null }));
vi.mock("@/components/comments/Comments", () => ({ CommentEditor: () => null }));
vi.mock("@/features/knowledge/api/catalog", () => ({ listKnowledgeBases: vi.fn(() => Promise.resolve([])) }));

import { FacetSidebar, toggleFacet } from "@/components/catalog/FacetSidebar";
import MarketBrowser from "@/components/catalog/MarketBrowser";
import { ItemCard } from "@/components/catalog/ItemCard";
import { parseMarketQuery } from "@/components/catalog/market-query";
import { ReviewsSection } from "@/components/catalog/Reviews";
import { TaxonomyPicker, type Classification } from "@/components/catalog/TaxonomyPicker";
import { classificationLabel, indexTaxonomy, pickName, resetTaxonomyCache, subjectLabel } from "@/components/catalog/useTaxonomy";
import { notificationText } from "@/components/notifications/NotificationBell";
import { SetEditor } from "@/components/practice-sets/SetEditor";
import { GradePanel } from "@/components/tasks/TasksHub";
import { downloadAuthenticated, suggestedFilename } from "@/lib/authenticated-download";
import { marketQueryString, type CatalogEntry, type ItemReview, type ItemReviews, type Taxonomy } from "@/lib/catalog-api";
import type { AppNotification } from "@/lib/notifications-api";
import { provenanceLabel } from "@/lib/provenance";
import type { TaskDetail } from "@/lib/tasks-api";

const taxonomy: Taxonomy = {
  stages: [
    { id: "primary", names: { zh: "小学", en: "Primary school", de: "Grundschule" },
      subjects: [{ id: "primary.math", names: { zh: "数学", en: "Mathematics", de: "Mathematik" }, children: [] }] },
    { id: "university", names: { zh: "大学", en: "University" },
      subjects: [{ id: "university.eng", names: { zh: "工学", en: "Engineering" },
        children: [{ id: "university.eng.cs", names: { zh: "计算机类", en: "Computing" } }] }] },
  ],
  difficulties: [{ id: "beginner", names: { zh: "入门", en: "Beginner" } }],
  grades: [
    { id: "grade.1", stage: "primary", names: { zh: "一年级", en: "Grade 1" } },
    { id: "grade.2", stage: "primary", names: { zh: "二年级", en: "Grade 2" } },
    { id: "grade.3", stage: "primary", names: { zh: "三年级", en: "Grade 3" } },
    { id: "grade.4", stage: "primary", names: { zh: "四年级", en: "Grade 4" } },
  ],
};
const index = indexTaxonomy(taxonomy);

const review = (over: Partial<ItemReview> = {}): ItemReview => ({
  user_id: "u_sara", author: "sara", stars: 4, text: "Clear.", reply: null, reply_at: null, created_at: 1, updated_at: 1,
  mine: false, can_reply: false, can_delete: false, ...over,
});
const reviews = (over: Partial<ItemReviews> = {}): ItemReviews => ({
  average: 4, count: 1, reviews: [review()], mine: null, can_review: false, ...over,
});

const entry = (over: Partial<CatalogEntry> = {}): CatalogEntry => ({
  kind: "practice_set", id: "set_1", owner_id: "u_t", status: "listed", title: "Waves", summary: "",
  stages: ["primary"], subjects: ["primary.math"], grades: ["grade.3"], difficulty: "beginner", tags: [],
  approved_once: true, migrated: false, reason: "", submitted_at: null, listed_at: 1, updated_at: 1,
  needs_classification: false, learners: 4, author: "tina", author_role: "teacher", ...over,
});

beforeEach(() => {
  for (const group of [api, tasks, practice]) for (const mock of Object.values(group)) mock.mockReset();
  router.replace.mockReset();
  resetTaxonomyCache();
  api.getTaxonomy.mockResolvedValue(taxonomy);
  search.value = "";
  listingStatus.value = "";
});
afterEach(() => vi.restoreAllMocks());

// ── reviews ──

it("offers the review form only to who may review, and saves stars with the text", async () => {
  api.listReviews.mockResolvedValue(reviews());
  const { unmount } = render(<ReviewsSection kind="practice_set" itemId="set_1" />);
  expect(await screen.findByText("Clear.")).toBeTruthy();
  expect(screen.getByTestId("review-average").textContent).toContain("Reviews: 1");
  expect(screen.queryByTestId("review-form")).toBeNull();
  expect(screen.queryByRole("button", { name: "Reply" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  unmount();

  api.listReviews.mockResolvedValue(reviews({ can_review: true }));
  api.saveMyReview.mockResolvedValue(reviews({ count: 2, average: 4.5, can_review: true,
    mine: review({ user_id: "me", stars: 5, mine: true, can_delete: true }),
    reviews: [review({ user_id: "me", stars: 5, text: "Great", mine: true, can_delete: true }), review()] }));
  render(<ReviewsSection kind="practice_set" itemId="set_1" />);
  const form = await screen.findByTestId("review-form");
  const save = within(form).getByRole("button", { name: "Save review" }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.click(within(form).getByRole("radio", { name: "5 of 5 stars" }));
  fireEvent.change(within(form).getByLabelText("What did you think? (optional)"), { target: { value: " Great " } });
  fireEvent.click(save);
  await waitFor(() => expect(api.saveMyReview).toHaveBeenCalledWith("practice_set", "set_1", 5, "Great"));
  expect(await screen.findByRole("button", { name: "Edit review" })).toBeTruthy();
  expect(screen.queryByTestId("review-form")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Edit review" }));
  expect(screen.getByTestId("review-form")).toBeTruthy();
});

it("lets the author reply to a review and an administrator delete anyone's", async () => {
  api.listReviews.mockResolvedValue(reviews({ reviews: [review({ can_reply: true })] }));
  api.replyToReview.mockResolvedValue(reviews({ reviews: [review({ can_reply: true, reply: "Thanks!", reply_at: 2 })] }));
  const { unmount } = render(<ReviewsSection kind="course" itemId="crs_1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Reply" }));
  fireEvent.change(screen.getByLabelText("Your reply"), { target: { value: "Thanks!" } });
  fireEvent.click(screen.getByRole("button", { name: "Save reply" }));
  await waitFor(() => expect(api.replyToReview).toHaveBeenCalledWith("course", "crs_1", "u_sara", "Thanks!"));
  expect(within(await screen.findByTestId("review-reply")).getByText("Thanks!")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Edit reply" })).toBeTruthy();
  unmount();

  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.listReviews.mockResolvedValue(reviews({ reviews: [review({ can_delete: true })] }));
  api.deleteReview.mockResolvedValue(reviews({ average: null, count: 0, reviews: [] }));
  render(<ReviewsSection kind="course" itemId="crs_1" />);
  fireEvent.click(await screen.findByRole("button", { name: "Delete the review by sara" }));
  await waitFor(() => expect(api.deleteReview).toHaveBeenCalledWith("course", "crs_1", "u_sara"));
  expect(await screen.findByText("No reviews yet.")).toBeTruthy();
  expect(api.deleteMyReview).not.toHaveBeenCalled();
});

it("words a new-review notification, counting repeats", () => {
  const note = (count: number): AppNotification =>
    ({ id: "n", kind: "listing_reviewed", params: { content: "course", title: "Physics" }, count, link: "", read: false, created_at: 1, updated_at: 1 }) as unknown as AppNotification;
  expect(notificationText(note(1), t)).toBe("Someone reviewed “Physics”");
  expect(notificationText(note(3), t)).toBe("3 new reviews of “Physics”");
});

// ── favorites and the rating sort ──

it("keeps a card in the favorites with its heart, and shows its rating", async () => {
  api.setFavorite.mockResolvedValue({ favorited: true });
  render(<ItemCard entry={entry({ rating: 4.25, rating_count: 12 })} nameOf={(id) => id} />);
  expect(screen.getByTestId("card-rating").textContent).toContain("4.3 (12)");
  const heart = screen.getByRole("button", { name: "Add to favorites" });
  expect(heart.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(heart);
  await waitFor(() => expect(api.setFavorite).toHaveBeenCalledWith("practice_set", "set_1", true));
  expect((await screen.findByRole("button", { name: "Remove from favorites" })).getAttribute("aria-pressed")).toBe("true");
});

it("has a My favorites tab and a Highest rated sort in the address", async () => {
  api.searchMarket.mockResolvedValue({ total: 0, page: 1, page_size: 24, items: [],
    facets: { stages: {}, subjects: {}, grades: {}, kinds: {}, difficulties: {}, tags: [], authors: [] } });
  const { unmount } = render(<MarketBrowser />);
  fireEvent.click(await screen.findByRole("tab", { name: "My favorites" }));
  expect(router.replace).toHaveBeenLastCalledWith("/market?favorites=1");
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "rating" } });
  expect(router.replace).toHaveBeenLastCalledWith("/market?sort=rating");
  unmount();

  search.value = "favorites=1&sort=rating";
  render(<MarketBrowser />);
  await waitFor(() => expect(api.searchMarket).toHaveBeenLastCalledWith(expect.objectContaining({ favorites: true, sort: "rating" })));
  expect(screen.getByRole("tab", { name: "My favorites" }).getAttribute("aria-selected")).toBe("true");
  expect(await screen.findByText("No favorites yet. Use the heart on anything in the market to keep it here.")).toBeTruthy();
  expect(marketQueryString(parseMarketQuery(new URLSearchParams("sort=rating&favorites=1&grade=grade.1,grade.2"))))
    .toBe("grade=grade.1%2Cgrade.2&sort=rating&favorites=1");
});

// ── grades ──

it("picks up to 3 grades under a chosen stage that has them, and drops them with the stage", () => {
  const change = vi.fn();
  const value: Classification = { stages: ["university"], subjects: [], grades: [], difficulty: "" };
  const { rerender } = render(<TaxonomyPicker taxonomy={taxonomy} value={value} onChange={change} />);
  expect(screen.queryByTestId("grade-picker")).toBeNull();

  const primary: Classification = { stages: ["primary"], subjects: ["primary.math"], grades: ["grade.1", "grade.2", "grade.3"], difficulty: "" };
  rerender(<TaxonomyPicker taxonomy={taxonomy} value={primary} onChange={change} />);
  const picker = screen.getByTestId("grade-picker");
  expect((within(picker).getByRole("button", { name: "Grade 4" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(within(picker).getByRole("button", { name: "Grade 2" }));
  expect(change).toHaveBeenLastCalledWith({ ...primary, grades: ["grade.1", "grade.3"] });
  fireEvent.click(screen.getByRole("button", { name: "Primary school" }));
  expect(change).toHaveBeenLastCalledWith({ stages: [], subjects: [], grades: [], difficulty: "" });
});

it("shows the grade filter only once a stage with grades is chosen", () => {
  const facets = { stages: { primary: 3, university: 2 }, subjects: { "primary.math": 3, "university.eng": 2 }, grades: { "grade.1": 2, "grade.3": 1 },
    kinds: {}, difficulties: {}, tags: [], authors: [] };
  const query = parseMarketQuery(new URLSearchParams(""));
  const { rerender } = render(<FacetSidebar query={query} facets={facets} taxonomy={taxonomy} index={index} onChange={vi.fn()} />);
  expect(screen.queryByRole("group", { name: "Grade" })).toBeNull();
  const onChange = vi.fn();
  const withStage = parseMarketQuery(new URLSearchParams("stage=primary"));
  rerender(<FacetSidebar query={withStage} facets={facets} taxonomy={taxonomy} index={index} onChange={onChange} />);
  const grades = screen.getByRole("group", { name: "Grade" });
  expect(within(grades).getAllByRole("checkbox").map((b) => b.textContent)).toEqual(["Grade 12", "Grade 31"]);
  fireEvent.click(within(grades).getByRole("checkbox", { name: /Grade 3/ }));
  expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ grade: ["grade.3"] }));
  // Dropping the stage drops its grades.
  const chosen = parseMarketQuery(new URLSearchParams("stage=primary&grade=grade.3&subject=primary.math"));
  expect(toggleFacet(chosen, "stage", "primary", index)).toMatchObject({ stage: [], subject: [], grade: [] });
});

// ── subjects by stage, names in the interface language ──

it("names a subject with its stage outside one stage, and picks the interface language", () => {
  expect(subjectLabel("primary.math", index, "zh-CN", true)).toBe("小学 · 数学");
  expect(subjectLabel("university.eng.cs", index, "en", true)).toBe("University · Engineering · Computing");
  expect(subjectLabel("primary.math", index, "en", false)).toBe("Mathematics");
  expect(pickName({ zh: "数学", en: "Mathematics", de: "Mathematik" }, "de")).toBe("Mathematik");
  expect(pickName({ zh: "数学", en: "Mathematics" }, "fr")).toBe("Mathematics");
  expect(classificationLabel({ stages: ["primary", "university"], subjects: ["primary.math", "university.eng"], grades: ["grade.3"] }, index, "en"))
    .toBe("Primary school · Grade 3 · Mathematics / University · Engineering");

  const facets = { stages: { primary: 3, university: 2 }, subjects: { "primary.math": 3, "university.eng": 2 }, kinds: {}, difficulties: {}, tags: [], authors: [] };
  const { rerender } = render(<FacetSidebar query={parseMarketQuery(new URLSearchParams(""))} facets={facets} taxonomy={taxonomy} index={index} onChange={vi.fn()} />);
  const subjects = () => screen.getByRole("group", { name: "Subject" });
  expect(within(subjects()).getByRole("checkbox", { name: /Primary school · Mathematics/ })).toBeTruthy();
  rerender(<FacetSidebar query={parseMarketQuery(new URLSearchParams("stage=primary"))} facets={facets} taxonomy={taxonomy} index={index} onChange={vi.fn()} />);
  expect(within(subjects()).queryByRole("checkbox", { name: /Primary school/ })).toBeNull();
  expect(within(subjects()).getByRole("checkbox", { name: /Mathematics/ })).toBeTruthy();
});

it("labels a teacher's content as published by them", () => {
  expect(provenanceLabel({ kind: "teacher", name: "tina" }, t, true)).toBe("Published by tina");
  expect(provenanceLabel({ kind: "school", name: "" }, t, true)).toBe("Published by the platform");
});

// ── remedial into the course ──

it("adds a published remedial set to the course it came from, once", async () => {
  practice.getSet.mockResolvedValue({ id: "ps_r", title: "Remedial: Fractions", description: "", status: "published", current_revision: 1,
    due_at: null, reveal: "after_submit", created_at: 1, updated_at: 1, questions: [] });
  tasks.getCourse.mockResolvedValue({ id: "crs_1", title: "Physics", tasks: [] });
  tasks.addCoursePracticeSet.mockResolvedValue({ id: "crs_1", title: "Physics" });
  render(<SetEditor setId="ps_r" courseId="crs_1" onChanged={vi.fn()} onDeleted={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "Add to course “Physics”" }));
  await waitFor(() => expect(tasks.addCoursePracticeSet).toHaveBeenCalledWith("crs_1", "ps_r"));
  expect(await screen.findByText("Added to the course")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add to course “Physics”" })).toBeNull();
});

it("offers no course button for a set already in the course", async () => {
  practice.getSet.mockResolvedValue({ id: "ps_1", title: "Week 1 quiz", description: "", status: "published", current_revision: 1,
    due_at: null, reveal: "after_submit", created_at: 1, updated_at: 1, questions: [] });
  tasks.getCourse.mockResolvedValue({ id: "crs_1", title: "Physics", tasks: [{ id: "t1", kind: "practice_set", target_id: "ps_1" }] });
  render(<SetEditor setId="ps_1" courseId="crs_1" onChanged={vi.fn()} onDeleted={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Save draft" })).toBeTruthy();
  await waitFor(() => expect(tasks.getCourse).toHaveBeenCalledWith("crs_1"));
  expect(screen.queryByTestId("add-to-course")).toBeNull();
});

it("offers no course button for a draft", async () => {
  practice.getSet.mockResolvedValue({ id: "ps_r", title: "Remedial", description: "", status: "draft", current_revision: 0,
    due_at: null, reveal: "after_submit", created_at: 1, updated_at: 1, questions: [] });
  tasks.getCourse.mockResolvedValue({ id: "crs_1", title: "Physics" });
  render(<SetEditor setId="ps_r" courseId="crs_1" onChanged={vi.fn()} onDeleted={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Save draft" })).toBeTruthy();
  expect(screen.queryByTestId("add-to-course")).toBeNull();
});

// ── practice in the market has no deadline ──

it("locks the deadline of a practice set that is in the market", async () => {
  listingStatus.value = "listed";
  practice.getSet.mockResolvedValue({ id: "ps_1", title: "Week 1 quiz", description: "", status: "published", current_revision: 1,
    due_at: null, reveal: "after_submit", created_at: 1, updated_at: 1, questions: [] });
  const { container } = render(<SetEditor setId="ps_1" onChanged={vi.fn()} onDeleted={vi.fn()} />);
  expect(await screen.findByText("Practice in the market has no deadline. Set deadlines on course tasks.")).toBeTruthy();
  expect((container.querySelector('input[type="datetime-local"]') as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("option", { name: "After the deadline" }) as HTMLOptionElement).disabled).toBe(true);
});

it("leaves the deadline open while the practice set is not in the market", async () => {
  listingStatus.value = "unlisted";
  practice.getSet.mockResolvedValue({ id: "ps_1", title: "Week 1 quiz", description: "", status: "published", current_revision: 1,
    due_at: null, reveal: "after_submit", created_at: 1, updated_at: 1, questions: [] });
  const { container } = render(<SetEditor setId="ps_1" onChanged={vi.fn()} onDeleted={vi.fn()} />);
  expect(await screen.findByRole("button", { name: "Save draft" })).toBeTruthy();
  expect(screen.queryByTestId("market-no-deadline")).toBeNull();
  expect((container.querySelector('input[type="datetime-local"]') as HTMLInputElement).disabled).toBe(false);
});

// ── grading the hand-in one read ──

const task = (attempt: number): TaskDetail => ({
  id: "t1", kind: "homework", title: "Essay", settings: { max_score: 10 },
  students: [{ id: "u_sara", username: "sara", in_scope: true, submission: {
    text: "My essay", files: [], attempt, submitted_at: 1, late: false, status: "submitted", score: null, comment: "",
    return_reason: "", graded_at: null } }],
} as unknown as TaskDetail);

it("grades and sends back the hand-in the teacher read", async () => {
  tasks.gradeHomework.mockResolvedValue(task(2));
  tasks.sendBackHomework.mockResolvedValue(task(2));
  render(<GradePanel task={task(2)} studentId="u_sara" onChanged={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Score"), { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Save grade" }));
  await waitFor(() => expect(tasks.gradeHomework).toHaveBeenCalledWith("t1", "u_sara", 8, "", 2));
  fireEvent.change(screen.getByLabelText("Why it goes back"), { target: { value: "Too short" } });
  fireEvent.click(screen.getByRole("button", { name: "Send back" }));
  await waitFor(() => expect(tasks.sendBackHomework).toHaveBeenCalledWith("t1", "u_sara", "Too short", 2));
});

it("says the student handed in again and reloads the task on a conflict", async () => {
  const conflict = Object.assign(new Error("The student handed in again just now. Reload before grading."), { status: 409 });
  tasks.gradeHomework.mockRejectedValue(conflict);
  tasks.getTask.mockResolvedValue(task(3));
  const changed = vi.fn();
  render(<GradePanel task={task(2)} studentId="u_sara" onChanged={changed} />);
  fireEvent.change(screen.getByLabelText("Score"), { target: { value: "8" } });
  fireEvent.click(screen.getByRole("button", { name: "Save grade" }));
  expect((await screen.findByRole("alert")).textContent).toBe("The student handed in again just now. Reload before grading.");
  await waitFor(() => expect(changed).toHaveBeenCalledWith(task(3)));
  expect(tasks.getTask).toHaveBeenCalledWith("t1");
});

// ── downloads with the sign-in credentials ──

it("downloads through the authenticated fetch and saves the blob under the server's name", async () => {
  const fetchMock = vi.fn(async () => new Response("a,b\n", {
    status: 200, headers: { "content-disposition": "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.csv" },
  }));
  vi.stubGlobal("fetch", fetchMock);
  const created = vi.fn(() => "blob:x");
  Object.assign(URL, { createObjectURL: created, revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    expect(this.download).toBe("报告.csv");
    expect(this.href).toBe("blob:x");
  });
  await downloadAuthenticated("/api/catalog/reports/course/crs_1/export", "report.csv");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [, init] = fetchMock.mock.calls[0] as unknown as [unknown, RequestInit];
  expect(init.credentials).toBe("include");
  expect(created).toHaveBeenCalled();
  expect(click).toHaveBeenCalled();
  expect(suggestedFilename('attachment; filename="results.csv"', "x")).toBe("results.csv");
  expect(suggestedFilename(null, "fallback.csv")).toBe("fallback.csv");

  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Not found." }), { status: 404 })));
  await expect(downloadAuthenticated("/api/x", "x.csv")).rejects.toThrow("Not found.");
  vi.unstubAllGlobals();
});
