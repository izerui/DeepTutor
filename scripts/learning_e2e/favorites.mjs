// SCENARIO="$PWD/scripts/learning_e2e/favorites.mjs" E2E_CONTEXT_PATH=/kaoyan bash scripts/learning_e2e/run.sh
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const { chromium } = await import(path.join(repo, "web/node_modules/playwright/index.mjs"));
const front = process.env.FRONT;
const out = path.join(process.env.STAGE, "out");
const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ locale: "en-US", viewport: { width: 1440, height: 1000 } });
const api = context.request;
const page = await context.newPage();
const json = async response => {
  assert.ok(response.ok(), `${response.status()} ${response.url()}: ${await response.text()}`);
  return response.json();
};
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const visit = async route => {
  for (let attempt = 0; ; attempt++) {
    try { return await page.goto(`${front}${route}`, { waitUntil: "load", timeout: 240000 }); }
    catch (error) { if (attempt >= 2 || !String(error).includes("ERR_ABORTED")) throw error; }
  }
};
const pass = text => console.log(`PASS ${text}`);

try {
  const credentials = {
    username: `favorites-${Date.now().toString(36)}`,
    password: "Temporary-favorites-e2e-2026!",
  };
  await json(await api.post(`${front}/api/auth/register`, { data: credentials }));
  await json(await api.post(`${front}/api/auth/login`, { data: credentials }));
  const { workspace } = await json(await api.post(`${front}/api/settings/workspace/registrations`, {
    data: { name: "Favorite source checks" },
  }));
  const scope = `dt_workspace=${encodeURIComponent(workspace.workspace_id)}`;
  const { notebook: source } = await json(await api.post(`${front}/api/notebooks?${scope}`, {
    data: { name: "Original collection" },
  }));
  const { notebook: target } = await json(await api.post(`${front}/api/notebooks?${scope}`, {
    data: { name: "Moved collection" },
  }));
  const original = "A preserved explanation about Fourier transforms.";
  const { record } = await json(await api.post(`${front}/api/notebooks/actions/add-record?${scope}`, {
    data: {
      notebook_ids: [source.id], record_type: "chat", title: "Fourier source",
      user_query: "Explain Fourier transforms", output: original, summary: "Original summary",
    },
  }));
  const sourceRoute = `/notebooks/${source.id}?record=${record.id}&${scope}`;
  await visit(sourceRoute);
  await page.getByRole("button", { name: "Edit record", exact: true }).waitFor({ timeout: 240000 });
  await page.getByRole("button", { name: "Edit record", exact: true }).click();
  const body = page.getByRole("textbox", { name: "Body", exact: true });
  assert.equal(await body.getAttribute("readonly"), "");
  assert.equal(await body.inputValue(), original);
  await page.getByRole("textbox", { name: "Record title", exact: true }).fill("Renamed source");
  await page.getByRole("textbox", { name: "Short summary", exact: true }).fill("A learner's summary");
  const save = page.waitForResponse(response =>
    response.request().method() === "PUT" && response.url().includes(`/records/${record.id}`));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  assert.equal((await save).status(), 200);
  let saved = (await json(await api.get(`${front}/api/notebooks/${source.id}?${scope}`))).records[0];
  assert.equal(saved.output, original);
  assert.equal(saved.title, "Renamed source");
  assert.equal(saved.summary, "A learner's summary");
  pass("editing metadata saves successfully while the original stays read-only");

  await page.getByRole("button", { name: "Add to a note", exact: true }).click();
  await page.getByRole("combobox", { name: "Target page" }).waitFor();
  await page.getByRole("button", { name: "Add to a note", exact: true }).click();
  const openNote = page.getByRole("link", { name: "Open note", exact: true });
  await openNote.waitFor({ timeout: 90000 });
  await openNote.click();
  const returnLink = page.getByRole("link", { name: "Return to source", exact: true });
  await returnLink.waitFor({ timeout: 240000 });
  assert.equal(new URL(page.url()).searchParams.get("dt_workspace"), workspace.workspace_id);
  await json(await api.post(`${front}/api/notebooks/${source.id}/records/${record.id}/actions/move?${scope}`, {
    data: { target_notebook_id: target.id },
  }));
  await returnLink.click();
  await page.waitForURL(url => url.pathname.endsWith(`/notebooks/${target.id}`), { timeout: 90000 });
  assert.equal(new URL(page.url()).searchParams.get("record"), record.id);
  assert.equal(new URL(page.url()).searchParams.get("dt_workspace"), workspace.workspace_id);
  await page.getByText(original, { exact: true }).waitFor({ timeout: 90000 });
  await page.screenshot({ path: path.join(out, "favorite-moved-source.png") });
  pass("a note's source link finds the moved favorite and preserves its workspace");

  await json(await api.delete(`${front}/api/notebooks/${source.id}?${scope}`));
  await visit(sourceRoute);
  await page.waitForURL(url => url.pathname.endsWith(`/notebooks/${target.id}`), { timeout: 90000 });
  await page.getByText(original, { exact: true }).waitFor({ timeout: 90000 });
  pass("the source still resolves after its old collection is deleted");

  await json(await api.delete(`${front}/api/notebooks/${target.id}/records/${record.id}?${scope}`));
  await visit(sourceRoute);
  await page.getByText("This favorite is no longer available.", { exact: true }).waitFor({ timeout: 90000 });
  pass("a deleted source reports that it is unavailable");
  assert.deepEqual(errors, []);
  pass("no browser runtime errors");
} catch (error) {
  await page.screenshot({ path: path.join(out, "favorites-failure.png") }).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
