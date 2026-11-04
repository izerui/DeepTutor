import { apiFetch } from "./api";

/**
 * The file name a server response suggests, or *fallback*. Prefers the
 * RFC 5987 ``filename*`` form so non-ASCII names (a Chinese title) survive.
 */
export function suggestedFilename(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      // Malformed percent-encoding: fall back to the plain form.
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain?.[1]?.trim() || fallback;
}

/**
 * Download an API file with the sign-in credentials ``apiFetch`` attaches
 * (follow-ups §7). A bare ``<a href download>`` sends cookies only, so a
 * deployment that signs in with a token alone could not download.
 */
export async function downloadAuthenticated(url: string, fallbackName: string): Promise<void> {
  const response = await apiFetch(url, { cache: "no-store" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data?.detail === "string" ? data.detail : `Download failed (${response.status})`);
  }
  const blob = await response.blob();
  const name = suggestedFilename(response.headers.get("content-disposition"), fallbackName);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Revoked a little later: revoking at once can cancel the download in some browsers.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}
