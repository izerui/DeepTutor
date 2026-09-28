import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const readWebFile = (...parts: string[]) =>
  readFileSync(path.join(process.cwd(), ...parts), "utf8");

test("voice settings expose the persisted reply auto-play preference", () => {
  const control = readWebFile(
    "features", "settings", "sections", "models", "VoicePlaybackPrefs.tsx",
  );
  const general = readWebFile("components", "settings", "SettingsOverview.tsx");
  const voice = readWebFile(
    "features",
    "settings",
    "sections",
    "models",
    "VoiceSettingsSection.tsx",
  );

  assert.ok(control.includes("useVoiceAutoplayPreference"));
  assert.ok(control.includes("useVoiceMathSpeakPreference"));
  assert.ok(control.includes("onChange={autoplay.setValue}"));
  assert.ok(!general.includes("VoiceAutoplaySetting"));
  assert.ok(!voice.includes("VoiceAutoplaySetting"));
  assert.ok(voice.includes("<VoicePlaybackPrefs />"));
  const store = readWebFile("features", "settings", "store", "SettingsStore.tsx");
  assert.ok(!store.includes("voice_autoplay"));
  assert.ok(!store.includes("updateVoiceAutoplay"));
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
