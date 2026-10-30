import { expect, it } from "vitest";

import { ensureLanguage, initI18n } from "@/i18n/init";

it("uses French copy now that the catalog is complete", async () => {
  const i18n = initI18n("fr");
  await ensureLanguage("fr");
  await i18n.changeLanguage("fr");
  // French is complete; a key only English has stands in for a missing translation.
  i18n.addResource("en", "app", "test.only_in_english", "Only in English");

  expect(i18n.t("common.save")).toBe("Enregistrer");
  expect(i18n.t("test.only_in_english")).toBe("Only in English");
});

it("uses English copy for a key missing from a partial locale", async () => {
  const i18n = initI18n("en");
  i18n.addResourceBundle(
    "partial",
    "app",
    { "common.cancel": "Annuler" },
    true,
    true,
  );
  await i18n.changeLanguage("partial");

  expect(i18n.t("common.cancel")).toBe("Annuler");
  expect(i18n.t("common.save")).toBe("Save");

  i18n.removeResourceBundle("partial", "app");
});

it("uses Ukrainian copy when a translation exists", async () => {
  const i18n = initI18n("uk");
  await ensureLanguage("uk");
  await i18n.changeLanguage("uk");

  expect(i18n.t("common.save")).toBe("зберегти");
});
