"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useSettings, type SettingsContextValue } from "./SettingsStore";

type UiSettingsSlice = Pick<
  SettingsContextValue,
  | "theme"
  | "language"
  | "responseLanguage"
  | "voiceAutoplay"
  | "codeBlockTheme"
  | "codeBlockShowLineNumbers"
  | "codeBlockWrapLongLines"
  | "updateTheme"
  | "updateLanguage"
  | "updateResponseLanguage"
  | "updateVoiceAutoplay"
  | "updateCodeBlockTheme"
  | "updateCodeBlockShowLineNumbers"
  | "updateCodeBlockWrapLongLines"
>;

const UiSettingsContext = createContext<UiSettingsSlice | null>(null);

export function UiSettingsProvider({ children }: { children: ReactNode }) {
  const source = useSettings();
  const value = useMemo<UiSettingsSlice>(
    () => ({
      theme: source.theme,
      language: source.language,
      responseLanguage: source.responseLanguage,
      voiceAutoplay: source.voiceAutoplay,
      codeBlockTheme: source.codeBlockTheme,
      codeBlockShowLineNumbers: source.codeBlockShowLineNumbers,
      codeBlockWrapLongLines: source.codeBlockWrapLongLines,
      updateTheme: source.updateTheme,
      updateLanguage: source.updateLanguage,
      updateResponseLanguage: source.updateResponseLanguage,
      updateVoiceAutoplay: source.updateVoiceAutoplay,
      updateCodeBlockTheme: source.updateCodeBlockTheme,
      updateCodeBlockShowLineNumbers: source.updateCodeBlockShowLineNumbers,
      updateCodeBlockWrapLongLines: source.updateCodeBlockWrapLongLines,
    }),
    [
      source.theme,
      source.language,
      source.responseLanguage,
      source.voiceAutoplay,
      source.codeBlockTheme,
      source.codeBlockShowLineNumbers,
      source.codeBlockWrapLongLines,
      source.updateTheme,
      source.updateLanguage,
      source.updateResponseLanguage,
      source.updateVoiceAutoplay,
      source.updateCodeBlockTheme,
      source.updateCodeBlockShowLineNumbers,
      source.updateCodeBlockWrapLongLines,
    ],
  );
  return (
    <UiSettingsContext.Provider value={value}>
      {children}
    </UiSettingsContext.Provider>
  );
}

export function useUiSettings(): UiSettingsSlice {
  const value = useContext(UiSettingsContext);
  if (!value)
    throw new Error("useUiSettings must be used inside UiSettingsProvider");
  return value;
}
