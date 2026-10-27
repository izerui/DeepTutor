/**
 * B2-16 behavioural test: annotateFailureCode round-trip through localStorage.
 *
 * Simulates: store submission → annotate failure code + materials → reload
 * (readFailedSubmissions) → verify code, materials, and localized message.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fetchAuthStatus, invalidateAuthStatusCache } from "@/lib/auth";
import {
  annotateFailureCode,
  readFailedSubmissions,
  storeFailedSubmission,
  clearFailedSubmission,
} from "@/lib/failed-submissions";
import { teacherTurnErrorMessage } from "@/lib/teacher-path-messages";

const fixture = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/api", () => ({
  apiFetch: fixture.fetch,
  apiUrl: (path: string) => path,
  setRuntimeAuthEnabled: vi.fn(),
}));

async function principal(user: string) {
  invalidateAuthStatusCache();
  fixture.fetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      enabled: true,
      authenticated: true,
      user_id: user,
    }),
  });
  await fetchAuthStatus();
}

const SESSION = "session-abc";
const SUBMISSION = "sub-001";

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  fixture.fetch.mockReset();
  invalidateAuthStatusCache();
});

describe("annotateFailureCode round-trip", () => {
  it("stores failureCode and failureDetails, readable after simulated reload", async () => {
    await principal("student-1");

    // 1. Store a failed submission (simulates the pre-turn local persistence).
    const stored = storeFailedSubmission(SESSION, {
      submissionId: SUBMISSION,
      content: "What is Newton's third law?",
      requestSnapshot: { content: "What is Newton's third law?", capability: "mastery_tutor" },
    });
    expect(stored).toBe(SUBMISSION);

    // 2. Annotate with a teacher-path materials-unavailable refusal.
    const annotated = annotateFailureCode(SESSION, SUBMISSION, "teacher_materials_unavailable", {
      error_code: "teacher_materials_unavailable",
      materials: ["Physics notes", "Mechanics book"],
      turn_terminal: true,
      status: "failed",
    });
    expect(annotated).toBe(true);

    // 3. Simulate reload: read back from local storage.
    const records = readFailedSubmissions(SESSION);
    expect(records).toHaveLength(1);
    expect(records[0].failureCode).toBe("teacher_materials_unavailable");
    expect(records[0].failureDetails).toBeDefined();
    expect(records[0].failureDetails?.materials).toEqual(["Physics notes", "Mechanics book"]);

    // 4. The localized message must include the material names.
    const teacher = teacherTurnErrorMessage({
      error_code: records[0].failureCode!,
      ...records[0].failureDetails,
    });
    expect(teacher).not.toBeNull();
    expect(teacher!.key).toMatch(/Course materials are temporarily unavailable.*\{\{materials\}\}/);
    expect(teacher!.values?.materials).toBe("Physics notes · Mechanics book");
  });

  it("stores failureCode for course-unavailable without details", async () => {
    await principal("student-1");

    storeFailedSubmission(SESSION, {
      submissionId: SUBMISSION,
      content: "Hello",
      requestSnapshot: { content: "Hello", capability: "mastery_tutor" },
    });

    annotateFailureCode(SESSION, SUBMISSION, "teacher_path_unavailable");

    const records = readFailedSubmissions(SESSION);
    expect(records[0].failureCode).toBe("teacher_path_unavailable");
    expect(records[0].failureDetails).toBeUndefined();

    const teacher = teacherTurnErrorMessage({
      error_code: records[0].failureCode!,
    });
    expect(teacher).not.toBeNull();
    expect(teacher!.key).toMatch(/This course is no longer available/);
  });

  it("does not annotate when the record does not exist", async () => {
    await principal("student-1");
    const result = annotateFailureCode(SESSION, "nonexistent", "teacher_path_unavailable");
    expect(result).toBe(false);
  });

  it("strips non-string/non-string-array values from failureDetails", async () => {
    await principal("student-1");

    storeFailedSubmission(SESSION, {
      submissionId: SUBMISSION,
      content: "Hi",
      requestSnapshot: { content: "Hi", capability: "mastery_tutor" },
    });

    annotateFailureCode(SESSION, SUBMISSION, "teacher_materials_unavailable", {
      materials: ["Book A"],
      turn_terminal: true,       // boolean — stripped
      status: "failed",          // string — kept
      nested: { deep: "value" }, // object — stripped
      count: 42,                 // number — stripped
    });

    const records = readFailedSubmissions(SESSION);
    expect(records[0].failureDetails).toEqual({
      materials: ["Book A"],
      status: "failed",
    });
  });

  it("reason disappears once the submission is cleared (materials restored)", async () => {
    await principal("student-1");

    storeFailedSubmission(SESSION, {
      submissionId: SUBMISSION,
      content: "Question",
      requestSnapshot: { content: "Question", capability: "mastery_tutor" },
    });
    annotateFailureCode(SESSION, SUBMISSION, "teacher_materials_unavailable", {
      materials: ["Gone book"],
    });

    // Simulate successful retry → clearFailedSubmission.
    clearFailedSubmission(SESSION, SUBMISSION);

    const records = readFailedSubmissions(SESSION);
    expect(records).toHaveLength(0);
  });
});
