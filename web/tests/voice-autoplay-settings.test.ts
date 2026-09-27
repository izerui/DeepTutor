import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const readWebFile = (...parts: string[]) =>
  readFileSync(path.join(process.cwd(), ...parts), "utf8");

test("voice settings expose the persisted reply auto-play preference", () => {
  const control = readWebFile(
    "components",
    "settings",
    "VoiceAutoplaySetting.tsx",
  );
  const general = readWebFile("components", "settings", "SettingsOverview.tsx");
  const voice = readWebFile(
    "features",
    "settings",
    "sections",
    "models",
    "VoiceSettingsSection.tsx",
  );

  assert.ok(control.includes("useUiSettings"));
  assert.ok(control.includes('title={t("Auto-play replies")}'));
  assert.ok(control.includes("checked={voiceAutoplay}"));
  assert.ok(control.includes('ariaLabel={t("Auto-play replies")}'));
  assert.ok(
    control.includes("onChange={(next) => void updateVoiceAutoplay(next)}"),
  );
  assert.ok(!control.includes("useVoiceAutoplayPreference"));
  assert.ok(general.includes("<VoiceAutoplaySetting />"));
  assert.ok(voice.includes("<VoiceAutoplaySetting />"));
});

test("voice auto-play remains off by default on the backend", () => {
  const source = readWebFile(
    "..",
    "deeptutor",
    "api",
    "routers",
    "settings.py",
  );

  assert.match(source, /"voice_autoplay": False/);
});
