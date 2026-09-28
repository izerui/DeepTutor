"use client";

import { useTranslation } from "react-i18next";

import { ModelsWorkspace } from "@/components/settings/ModelsWorkspace";
import { SettingsPageHeader } from "@/components/settings/shared";
import { VoiceAutoplaySetting } from "@/components/settings/VoiceAutoplaySetting";
import { VoicePlaybackPrefs } from "./VoicePlaybackPrefs";
export default function VoiceSettingsPage() {
  const { t } = useTranslation();

  return (
    <div className="space-y-8">
      <SettingsPageHeader
        title={t("Voice")}
        description={t(
          "Manage speech synthesis and transcription models using saved providers.",
        )}
      />
      <VoiceAutoplaySetting />
      <VoicePlaybackPrefs includeAutoplay={false} />
      <ModelsWorkspace page="voice" />
    </div>
  );
}
