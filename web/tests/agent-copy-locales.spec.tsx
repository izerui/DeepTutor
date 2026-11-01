import { act } from "@testing-library/react";
import { afterEach, beforeAll, expect, it } from "vitest";

import { ensureLanguage, initI18n } from "@/i18n/init";

// Adapted from upstream be7b58ee0: develop has no connected-agents page, so only
// the subagent settings/transcript keys are checked.
const i18n = initI18n("en");

beforeAll(async () => {
  await ensureLanguage("zh");
  await ensureLanguage("de");
});

afterEach(async () => {
  await act(async () => {
    await i18n.changeLanguage("en");
  });
});

it("keeps the Chinese text of the interpolated strings unchanged", async () => {
  await i18n.changeLanguage("zh");
  // These used to be template literals in the components; the zh catalog now holds them.
  expect(
    i18n.t(
      "Model, reasoning effort, and run parameters DeepTutor drives the local {{name}} with when consulting it. These override the CLI defaults; leave blank to keep the CLI's own default.",
      { name: "Codex" },
    ),
  ).toBe(
    "DeepTutor 通过 consult_subagent 调用本机 Codex 时使用的模型、推理强度与运行参数。设置后即覆盖 CLI 的默认值；留空表示沿用 CLI 默认。",
  );
  expect(i18n.t("Last synced: {{when}}", { when: "2026年11月1日" })).toBe(
    "上次同步：2026年11月1日",
  );
});

it("resolves the subagent copy through the German catalog", async () => {
  await i18n.changeLanguage("de");
  expect(i18n.t("Last synced: {{when}}", { when: "1. Nov." })).toBe(
    "Zuletzt synchronisiert: 1. Nov.",
  );
  expect(i18n.t("collapse")).not.toBe("collapse");
  expect(i18n.t("… +{{count}} lines", { count: 3 })).toContain("3");
});
