// Run with SCENARIO="$PWD/scripts/learning_e2e/notes-calendar.mjs" bash scripts/learning_e2e/run.sh.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const { chromium } = await import(path.join(repo, "web/node_modules/playwright/index.mjs"));
const front = process.env.FRONT;
const out = path.join(process.env.STAGE, "out");
const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ locale: "en-US", viewport: { width: 1440, height: 1000 } });
const api = context.request;
const json = async response => {
  assert.equal(response.ok(), true, `${response.url()}: ${response.status()} ${await response.text()}`);
  return response.json();
};
const password = "Temporary-notes-e2e-2026!";
const runId = Date.now().toString(36);
const username = process.env.NOTES_USERNAME || `notes-${runId}`;
const errors = [];
const page = await context.newPage();
page.on("pageerror", error => errors.push(error.message));
const visit = async route => {
  for (let attempt = 0; ; attempt++) {
    try { return await page.goto(`${front}${route}`, { waitUntil: "load", timeout: 240000 }); }
    catch (error) { if (attempt >= 2 || !String(error).includes("ERR_ABORTED")) throw error; }
  }
};
const shot = name => page.screenshot({ path: path.join(out, `${name}.png`) });
const pass = message => console.log(`PASS ${message}`);
try {
  if (!process.env.NOTES_USERNAME)
    await json(await api.post(`${front}/api/auth/register`, { data: { username, password } }));
  await json(await api.post(`${front}/api/auth/login`, { data: { username, password } }));
  await visit("/co-writer");
  await page.getByRole("button", { name: "New page", exact: true }).first().waitFor({ timeout: 240000 });
  pass("the notebook library opens from its existing route");
  const legacy = await json(await api.post(`${front}/api/documents`, {
    data: { title: "Legacy migration", content: "# Old Markdown\n\nKeep my original text." },
  }));
  await visit(`/co-writer/${legacy.id}`);
  const editor = page.locator(".bn-editor[contenteditable=true]");
  await editor.waitFor({ timeout: 240000 });
  assert.match(await editor.innerText(), /Keep my original text/);
  await editor.click();
  await page.keyboard.press("ControlOrMeta+End");
  await page.keyboard.press("Enter");
  await page.keyboard.insertText("A newly saved paragraph.");
  await page.waitForFunction(async ({ front, id }) => {
    const response = await fetch(`${front}/api/documents/${id}`);
    const document = await response.json();
    return document.content?.includes("A newly saved paragraph.");
  }, { front, id: legacy.id }, { timeout: 30000 });
  await page.reload({ waitUntil: "load" });
  await editor.waitFor({ timeout: 90000 });
  assert.match(await editor.innerText(), /A newly saved paragraph/);
  await shot("notes-legacy-reload");
  pass("legacy Markdown opens, edits save, and blocks survive reload");

  if (process.env.NOTES_AI === "1") {
    await page.getByRole("button", { name: "Ask Dito", exact: true }).click();
    const composer = page.getByRole("textbox", { name: "Message Dito", exact: true });
    await composer.waitFor({ timeout: 90000 });
    await composer.fill("NOTE-EDIT Please propose an addition to this page.");
    await composer.press("Enter");
    await page.getByText("Review the proposed addition.", { exact: true }).first().waitFor({ timeout: 90000 });
    const before = await json(await api.get(`${front}/api/documents/${legacy.id}`));
    assert.equal(before.content.includes("An assistant addition"), false);
    const proposals = await json(await api.get(`${front}/api/documents/${legacy.id}/suggestions`));
    assert.equal(proposals.suggestions.filter(item => item.status === "pending").length, 1);
    await shot("notes-assistant-review");
    await page.getByRole("button", { name: "Accept", exact: true }).click();
    await page.waitForFunction(async ({ front, id }) => {
      const response = await fetch(`${front}/api/documents/${id}`);
      return (await response.json()).content?.includes("An assistant addition");
    }, { front, id: legacy.id }, { timeout: 30000 });
    pass("the note companion proposes edits and changes the page only after acceptance");
  }

  const flashcards = await json(await api.post(`${front}/api/documents`, {
    data: { title: "Calculus cards", blocks: [{
      id: "cards", type: "learning",
      props: { kind: "flashcards", data: JSON.stringify({ cards: [
        { front: "Derivative of $x^2$?", back: "$2x$" },
      ] }) },
      children: [],
    }] },
  }));
  await visit(`/co-writer/${flashcards.id}`);
  await editor.waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "More page actions" }).waitFor({ timeout: 90000 });
  await page.getByRole("button", { name: "More page actions" }).click();
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("menuitem", { name: "Export flashcards (Anki)" }).click(),
  ]);
  const exported = path.join(out, "flashcards.txt");
  await file.saveAs(exported);
  const cards = fs.readFileSync(exported, "utf8");
  assert.match(cards, /#guid column:5/);
  assert.match(cards, /Derivative of/);
  assert.match(cards, /\\\(2x\\\)/);
  pass("Anki download contains stable card IDs and rendered math");
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("notes-mobile");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  await page.setViewportSize({ width: 1440, height: 1000 });

  await visit("/calendar");
  const newEvent = page.getByRole("button", { name: "New event", exact: true });
  await newEvent.waitFor({ timeout: 240000 });
  await newEvent.click();
  await page.getByRole("textbox", { name: "Event title" }).fill("Weekly calculus review");
  await page.getByRole("combobox", { name: "Repeat", exact: true }).selectOption("weekly");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await page.getByText("Weekly calculus review", { exact: true }).first().waitFor({ timeout: 30000 });
  await page.reload({ waitUntil: "load" });
  await page.getByText("Weekly calculus review", { exact: true }).first().waitFor({ timeout: 90000 });
  const calendarExport = await api.get(`${front}/api/calendar/export.ics`);
  assert.equal(calendarExport.ok(), true);
  const ics = await calendarExport.text();
  assert.match(ics, /BEGIN:VCALENDAR/);
  assert.match(ics, /SUMMARY:Weekly calculus review/);
  assert.match(ics, /FREQ=WEEKLY/);
  fs.writeFileSync(path.join(out, "calendar.ics"), ics);
  await shot("calendar-desktop");
  pass("recurring calendar event saves, reloads, and exports as ICS");
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("calendar-mobile");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  pass("notes and calendar fit mobile viewports");

  if (process.env.NOTES_AI === "1") {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const { workspace } = await json(await api.post(`${front}/api/settings/workspace/registrations`, {
      data: { name: `Notebook workspace ${runId}` },
    }));
    assert.ok(workspace.workspace_id);
    const scope = `?dt_workspace=${encodeURIComponent(workspace.workspace_id)}`;
    const first = await json(await api.post(`${front}/api/documents${scope}`, {
      data: { title: "Scoped first page", blocks: [] },
    }));
    const second = await json(await api.post(`${front}/api/documents${scope}`, {
      data: { title: "Scoped second page", blocks: [] },
    }));
    const uploaded = await json(await api.post(`${front}/api/documents/${second.id}/assets${scope}`, {
      multipart: { file: {
        name: "pixel.png", mimeType: "image/png",
        buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=", "base64"),
      } },
    }));
    await json(await api.put(`${front}/api/documents/${second.id}${scope}`, {
      data: { expected_revision: second.revision, blocks: [{
        id: "uploaded-image", type: "image",
        props: { url: uploaded.url, name: "Uploaded pixel", caption: "Uploaded pixel", previewWidth: 64 },
        children: [],
      }] },
    }));
    await visit(`/co-writer/${first.id}${scope}`);
    const pages = page.locator('nav[aria-label="Breadcrumb"] select');
    await pages.waitFor({ timeout: 90000 });
    await pages.selectOption(second.id);
    await page.waitForURL(url => url.pathname.endsWith(`/co-writer/${second.id}`), { timeout: 30000 });
    assert.equal(new URL(page.url()).searchParams.get("dt_workspace"), workspace.workspace_id);
    await page.waitForFunction(() => [...document.querySelectorAll("img")].some(image =>
      image.src.includes("/assets/") && image.complete && image.naturalWidth > 0
    ), null, { timeout: 30000 });
    const src = await page.locator('img[src*="/assets/"]').first().getAttribute("src");
    assert.ok(src.startsWith(`${new URL(front).pathname}/api/`), src);
    await shot("notes-workspace-media");
    pass("page navigation retains the workspace and uploaded images load under the deployment path");
  }

  const other = await browser.newContext();
  await json(await other.request.post(`${front}/api/auth/register`, { data: { username: `other-${runId}`, password } }));
  await json(await other.request.post(`${front}/api/auth/login`, { data: { username: `other-${runId}`, password } }));
  assert.equal((await other.request.get(`${front}/api/documents/${flashcards.id}`)).status(), 404);
  assert.equal((await other.request.get(`${front}/api/documents/${flashcards.id}/flashcards/export`)).status(), 404);
  const otherCalendar = await other.request.get(`${front}/api/calendar/export.ics`);
  assert.equal((await otherCalendar.text()).includes("Weekly calculus review"), false);
  await other.close();
  pass("another account cannot read notes, export cards, or see calendar events");
  assert.deepEqual(errors, [], "Browser runtime errors");
  pass("no browser runtime errors");
} catch (error) {
  await shot("notes-calendar-failure").catch(() => {});
  throw error;
} finally {
  await browser.close();
}
