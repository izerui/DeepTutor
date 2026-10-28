import { apiFetch, apiUrl } from "@/lib/api";
import { withClientCache } from "@/lib/client-cache";
import { learningShellActive } from "@/lib/learning-shell";

import {
  parseCapabilityCatalogPayload,
  type CapabilityDescriptor,
} from "./model";

export async function fetchCapabilityCatalog(options?: {
  force?: boolean;
}): Promise<CapabilityDescriptor[]> {
  return withClientCache(
    "capabilities:catalog",
    async () => {
      // The registry is outside the learner surface (403). An empty catalog
      // leaves callers on their chat-only fallback, which is what the 403
      // produced before, without the denied request.
      if (await learningShellActive()) return [];
      const response = await apiFetch(apiUrl("/api/capabilities/registered"));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return parseCapabilityCatalogPayload(await response.json());
    },
    { force: options?.force, ttlMs: 300_000 },
  );
}
