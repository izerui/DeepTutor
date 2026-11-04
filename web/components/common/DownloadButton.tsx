"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { actionClass } from "./form-styles";

/**
 * A download fetched with the sign-in credentials, then saved (follow-ups §7),
 * instead of a bare ``<a href download>`` that a token-only deployment refuses.
 */
export function DownloadButton({
  download,
  label,
  className = actionClass,
  icon = false,
}: {
  download: () => Promise<void>;
  label: string;
  className?: string;
  icon?: boolean;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <>
      <button type="button" className={className} disabled={busy}
        onClick={() => {
          setBusy(true);
          setError("");
          download()
            .catch((err) => setError(err instanceof Error ? t(err.message) : String(err)))
            .finally(() => setBusy(false));
        }}>
        {icon && <Download className="h-3.5 w-3.5" aria-hidden />}
        {busy ? t("Downloading…") : label}
      </button>
      {error && <span role="alert" className="text-[12px] text-red-600">{error}</span>}
    </>
  );
}
