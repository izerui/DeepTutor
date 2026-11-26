// Learning-loop acceptance scenario. Run through run.sh, which provides
// STAGE (the synthetic data root) and FRONT (the frontend URL).
//
// Each step is checked twice: on screen in a real browser, and against the
// backend's own records, so a page that looks right over wrong data fails.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");
const { chromium } = await import(path.join(repo, "web/node_modules/playwright/index.mjs"));

const STAGE = process.env.STAGE;
const F = process.env.FRONT;
const OUT = path.join(STAGE, "out");
const PASSWORD = "Temporary-e2e-only-2026!";
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: Boolean(ok) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
};
const modelRequests = () =>
  fs.existsSync(path.join(STAGE, "model-requests.jsonl"))
    ? fs.readFileSync(path.join(STAGE, "model-requests.jsonl"), "utf8").trim().split("\n").filter(Boolean).map(JSON.parse)
    : [];
const lastUserText = (body) => {
  const users = (body.messages || []).filter((m) => m.role === "user");
  const content = users.at(-1)?.content;
  return typeof content === "string" ? content : JSON.stringify(content ?? "");
};

const browser = await chromium.launch({ channel: "chrome" });
const ctx = await browser.newContext({ locale: "en-US", viewport: { width: 1400, height: 900 } });
const api = ctx.request;
const json = async (response) => {
  if (!response.ok()) throw new Error(`${response.url()} -> ${response.status()} ${await response.text()}`);
  return response.json();
};
await json(await api.post(`${F}/api/auth/register`, { data: { username: "learner", password: PASSWORD } }));
await json(await api.post(`${F}/api/auth/login`, { data: { username: "learner", password: PASSWORD } }));
const page = await ctx.newPage();
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

// Dev servers mount twice (React strict mode), which can drop the very first
// send of a new conversation; production does not. Retry once, as a learner would.
async function send(text) {
  const box = page.locator("textarea").first();
  await box.click();
  await box.pressSequentially(text, { delay: 5 });
  await box.press("Enter");
  await page.waitForTimeout(4000);
  const retry = page.getByRole("button", { name: "Retry" });
  if (await retry.isVisible().catch(() => false)) await retry.click();
}

// A dev server compiling a route on first visit can abort that navigation.
async function visit(url) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await page.goto(url, { waitUntil: "load", timeout: 240000 });
    } catch (error) {
      if (attempt >= 2 || !String(error).includes("ERR_ABORTED")) throw error;
      await page.waitForTimeout(3000);
    }
  }
}

async function step(name, body) {
  try {
    await body();
  } catch (error) {
    check(name, false, String(error.message || error).split("\n")[0]);
    await shot(`failed-${name.replace(/\W+/g, "-")}`).catch(() => {});
  }
}

// 1. Chat: quote a passage of an answer and ask about it.
await step("chat quote", async () => {
  await visit(`${F}/chat`);
  await page.locator("textarea").first().waitFor({ timeout: 240000 });
  await page.waitForTimeout(3000);
  await send("请讲解 f(x)=x^3 的极值");
  const passage = page.getByText("第二步：令 f'(x)=0，解得驻点 x=0。").first();
  await passage.waitFor({ timeout: 90000 });
  await page.waitForURL(/\/chat\/[^/?]+/);
  await page.reload({ waitUntil: "load" });
  await passage.waitFor({ timeout: 90000 });
  const box = await passage.boundingBox();
  await page.mouse.move(box.x + 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 10 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Add to chat" }).first().click();
  await send("QUOTE-CHECK 为什么这里要令导数为 0？");
  await page.getByText("收到引用").first().waitFor({ timeout: 90000 });
  const sent = modelRequests().find((r) => (r.body.tools || []).length && lastUserText(r.body).includes("QUOTE-CHECK"));
  check("model receives the quote labelled before the question",
    sent && /Quoted Passages[\s\S]*第二步[\s\S]*QUOTE-CHECK/.test(lastUserText(sent.body)));
  await page.reload({ waitUntil: "load" });
  await page.getByText("收到引用").first().waitFor({ timeout: 120000 });
  const main = await page.locator("main").innerText();
  check("quote survives a reload", main.includes("第二步：令 f'(x)=0") && main.includes("QUOTE-CHECK"));
  await shot("1-chat-quote");
});

// 2. Mastery path: a posed question, a wrong answer, a recorded mistake.
let pathId = "";
let kpId = "";
await step("mastery question", async () => {
  const topic = await json(await api.post(`${F}/api/mastery-paths/topics`, {
    data: { name: "导数入门", goal: "掌握导数的基本计算",
      modules: [{ name: "导数", knowledge_points: [{ name: "幂函数求导", type: "memory" }] }] },
  }));
  pathId = topic.path_id;
  kpId = topic.map.modules[0].knowledge_points[0].id;
  fs.writeFileSync(path.join(STAGE, "kp.txt"), kpId);
  await visit(`${F}/learning/mastery/${pathId}`);
  await page.getByText("Next up", { exact: true }).waitFor({ timeout: 120000 });
  await page.getByRole("link", { name: /Begin first waypoint/ }).click();
  await page.locator("textarea").first().waitFor({ timeout: 240000 });
  await page.waitForTimeout(4000);
  await send("MQ-ASK 请出一道题");
  await page.getByText("x 的四次方在 x=1 处的导数是多少？").first().waitFor({ timeout: 120000 });
  check("tutor poses a question card", true);
  await shot("2-question");
  await page.getByRole("button", { name: /^[AB]\s*3$/ }).click();
  await page.getByRole("button", { name: "Submit" }).first().click();
  await page.waitForTimeout(8000);
  await shot("2-answered");
  const report = (await json(await api.get(`${F}/api/mastery-paths/progress/${pathId}/objectives/${kpId}`))).objective;
  check("wrong answer is graded and kept", report.attempts.length === 1 && report.attempts[0].is_correct === false,
    `attempts=${report.attempts.length}`);
  check("wrong answer opens a mistake", report.errors.some((e) => e.status === "active"));
  check("no review time is promised before mastery", report.review === null || report.review.due_at === null);
});

// 3. Practice: the mistake is in the bank, linked; reviewing it right closes it
//    and reaches the path as review evidence.
await step("practice review", async () => {
  const bank = await json(await api.get(`${F}/api/question-notebook/entries?mastery_path_id=${pathId}`));
  const entry = bank.items.find((item) => item.knowledge_point_id === kpId);
  check("mistake lands in the question bank with its objective", Boolean(entry));
  const question = await json(await api.get(`${F}/api/question-notebook/practice/questions/${entry.id}`));
  await json(await api.post(`${F}/api/question-notebook/practice/questions/${entry.id}/review`, {
    data: { request_id: "e2e-practice-review-0001", version: question.state.version, rating: "good", answer: entry.correct_answer },
  }));
  const after = (await json(await api.get(`${F}/api/question-notebook/entries?mastery_path_id=${pathId}`)))
    .items.find((item) => item.id === entry.id);
  check("practising it to mastered resolves the mistake", after.resolved === true);
  const report = (await json(await api.get(`${F}/api/mastery-paths/progress/${pathId}/objectives/${kpId}`))).objective;
  const reviews = report.evidence.filter((item) => item.evidence_id === "practice:e2e-practice-review-0001");
  check("the review reaches the mastery path once", reviews.length === 1 && reviews[0].result === "correct",
    `evidence=${report.evidence.map((item) => item.evidence_id).join(",")}`);
});

// 4. Course: one ranked next step, shown on the page.
await step("course next step", async () => {
  const course = await json(await api.post(`${F}/api/courses`, { data: { name: "高等数学" } }));
  const courseId = course.id || course.course?.id;
  await json(await api.post(`${F}/api/courses/${courseId}/resources`, {
    data: { kind: "mastery_path", ref_id: pathId, label: "导数入门" },
  }));
  const state = await json(await api.get(`${F}/api/courses/${courseId}/state`));
  check("course state ranks next steps", Array.isArray(state.next_steps) && state.next_steps.length > 0,
    state.next_steps?.map((s) => s.kind).join(">"));
  await visit(`${F}/courses/${courseId}`);
  await page.waitForTimeout(6000);
  await shot("4-course");
  const top = state.next_steps[0];
  check("course recommends continuing this path", top.kind === "continue_path" && top.path_id === pathId);
  await page.getByRole("button", { name: "Keep going" }).click();
  await page.locator("textarea").first().waitFor({ timeout: 120000 });
  await page.waitForTimeout(4000);
  await page.locator("textarea").first().fill("");
  await send("MQ-RETEST 请用一道新题检测幂函数求导");
  await page.getByText("x 的五次方在 x=1 处的导数是多少？").last().waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: /^[AB]\s*3$/ }).last().click();
  await page.getByRole("button", { name: "Submit", exact: true }).last().click();

  const scopedBank = `${F}/api/question-notebook/entries?course_id=${courseId}`;
  let missed;
  for (let i = 0; i < 60 && !missed; i++) {
    const entries = (await json(await api.get(scopedBank))).items;
    missed = entries.find((item) => item.knowledge_point_id === kpId && !item.resolved);
    if (!missed) await page.waitForTimeout(500);
  }
  if (!missed) throw new Error("New mistake missing from course question bank");
  const progress = async () => (await json(await api.get(`${F}/api/mastery-paths/progress/${pathId}/objectives/${kpId}`))).objective;
  const failed = await progress();
  check("two independent questions produce two graded attempts", failed.attempts.length === 2 && failed.attempts.every(a => a.is_correct === false));
  const courseState = async () => json(await api.get(`${F}/api/courses/${courseId}/state`));
  const next = (await courseState()).next_steps[0];
  check("course prioritises the new mistake", ["practice_review", "open_mistakes"].includes(next.kind));
  await visit(`${F}/courses/${courseId}`);
  await page.getByRole("button", { name: next.kind === "practice_review" ? "Start review" : "Work through them" }).first().waitFor();
  const homePractice = page.getByRole("region", { name: "What needs your attention" }).getByRole("link", { name: /Practice/i });
  await visit(`${F}/learning`);
  await homePractice.waitFor();
  await homePractice.click();
  const questionUrl = `${F}/api/question-notebook/practice/questions/${missed.id}`;
  const question = await json(await api.get(questionUrl));
  const data = { request_id: "e2e-retest-review-0002", version: question.state.version, rating: "good", answer: missed.correct_answer };
  await json(await api.post(`${questionUrl}/review`, { data }));
  await json(await api.post(`${questionUrl}/review`, { data }));
  const reviewed = await progress();
  check("retrying the review counts once", reviewed.evidence.filter(item => item.evidence_id === `practice:${data.request_id}`).length === 1);
  check("course clears the new mistake after review", (await courseState()).question_bank.wrong === 0);
  await visit(`${F}/courses/${courseId}`);
  await page.getByRole("button", { name: "Keep going" }).waitFor();
  await visit(`${F}/learning`);
  await page.locator("#learning-recent").waitFor();
  check("learning home clears due practice after review", await homePractice.count() === 0);
});

await browser.close();
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed. Screenshots: ${OUT}`);
process.exit(failed.length ? 1 : 0);
