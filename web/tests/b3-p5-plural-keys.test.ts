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

function loadLocale(lang: string): Record<string, string> {
  return JSON.parse(
    fs.readFileSync(path.join(localeDir, lang, "app.json"), "utf8"),
  );
}

const en = loadLocale("en");

// Find all _one keys in en
const oneKeys = Object.keys(en).filter((k) => k.endsWith("_one"));

test("pl/uk locales have _one/_few/_many/_other for every en _one key", () => {
  const requiredSuffixes = ["_one", "_few", "_many", "_other"];
  for (const lang of ["pl", "uk"]) {
    const catalog = loadLocale(lang);
    const missing: string[] = [];
    for (const oneKey of oneKeys) {
      const stem = oneKey.slice(0, -4); // strip _one
      for (const suffix of requiredSuffixes) {
        const key = stem + suffix;
        if (!(key in catalog)) {
          missing.push(`${lang}: ${key}`);
        }
      }
    }
    assert.deepEqual(
      missing,
      [],
      `Missing plural keys in ${lang}:\n${missing.join("\n")}`,
    );
  }
});

test("fr locale has _one/_many/_other for every en _one key", () => {
  const requiredSuffixes = ["_one", "_many", "_other"];
  const catalog = loadLocale("fr");
  const missing: string[] = [];
  for (const oneKey of oneKeys) {
    const stem = oneKey.slice(0, -4);
    for (const suffix of requiredSuffixes) {
      const key = stem + suffix;
      if (!(key in catalog)) {
        missing.push(`fr: ${key}`);
      }
    }
  }
  assert.deepEqual(
    missing,
    [],
    `Missing plural keys in fr:\n${missing.join("\n")}`,
  );
});
