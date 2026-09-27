"use client";

import { useTranslation } from "react-i18next";

import { useUiSettings } from "@/features/settings/store";

import { SettingRow, SettingSection } from "./shared";
import { Toggle } from "./Toggle";

export function VoiceAutoplaySetting() {
  const { t } = useTranslation()
  const { voiceAutoplay, updateVoiceAutoplay } = useUiSettings()

  return (
    <SettingSection
      title={t("Playback")}
      description={t("How spoken replies behave in chat.")}
    >
      <SettingRow
        title={t("Auto-play replies")}
        description={t(
          "Read each assistant reply aloud automatically. When this is off, the speaker button can enable auto-play for the current conversation.",
        )}
        control={
          <Toggle
            checked={voiceAutoplay}
            ariaLabel={t("Auto-play replies")}
            onChange={(next) => void updateVoiceAutoplay(next)}
          />
        }
      />
    </SettingSection>
  );
}
