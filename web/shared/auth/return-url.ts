import { stripBasePath } from "@/lib/workspace-scope";

const RETURN_URL_BASE = "https://deeptutor.invalid";

export interface BrowserLocationParts {
  pathname: string;
  search?: string;
  hash?: string;
}

/** Return a same-origin application path or the supplied safe fallback. */
export function normalizeInternalReturnPath(
  raw: string | null | undefined,
  fallback = "/",
): string {
  const candidate = String(raw ?? "").trim();
  if (
    !candidate.startsWith("/") ||
    candidate.startsWith("//") ||
    candidate.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(candidate)
  ) {
    return fallback;
  }

  try {
    const parsed = new URL(candidate, RETURN_URL_BASE);
    if (parsed.origin !== RETURN_URL_BASE) return fallback;
    const decodedPath = decodeURIComponent(parsed.pathname);
    if (
      decodedPath.startsWith("//") ||
      decodedPath.includes("\\") ||
      /[\u0000-\u001f\u007f]/.test(decodedPath)
    ) {
      return fallback;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

export function browserReturnPath(location: BrowserLocationParts): string {
  const raw = `${location.pathname}${location.search ?? ""}${location.hash ?? ""}`;
  return normalizeInternalReturnPath(stripBasePath(raw));
}

export function loginHref(returnPath: string): string {
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
  const query = new URLSearchParams({
    next: normalizeInternalReturnPath(returnPath),
  });
  return `${basePath}/login?${query.toString()}`;
}

/**
 * Server redirects cannot read a fragment. Browsers retain it on the login
 * URL, so carry that fragment into a validated destination that lacks one.
 */
export function inheritLoginHash(returnPath: string, loginHash: string): string {
  const safe = normalizeInternalReturnPath(returnPath);
  if (!loginHash || safe.includes("#")) return safe;
  const hash = loginHash.startsWith("#") ? loginHash : `#${loginHash}`;
  return normalizeInternalReturnPath(`${safe}${hash}`);
}
