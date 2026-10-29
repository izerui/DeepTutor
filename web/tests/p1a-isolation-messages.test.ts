import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

function findLocaleDir(): string {
  let dir = __dirname;
  for (let i = 0; i < 8; i++) {
    const candidate = path.join(dir, "locales");
    if (fs.existsSync(candidate)) return candidate;
    dir = path.dirname(dir);
  }
  throw new Error("could not locate locales/ directory");
}

const localeDir = findLocaleDir();

const KEYS = [
  "This folder is the deployment's shared content folder and cannot store personal learning data.",
  "This folder belongs to another account.",
  "The selected workspace folder does not exist.",
  "Only one administrator is supported until each administrator has separate data.",
];

for (const lang of ["en", "zh", "de", "fr", "pl", "uk"]) {
  test(`${lang} translates every P1a isolation message`, () => {
    const table = JSON.parse(
      fs.readFileSync(path.join(localeDir, lang, "app.json"), "utf8"),
    ) as Record<string, string>;
    for (const key of KEYS) {
      assert.ok(table[key], `${lang}: ${key}`);
    }
  });
}
