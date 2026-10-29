"use client";

import MyAgentsSection from "@/components/space/MyAgentsSection";

/**
 * "My Agents" hub — the imported-history agents (replay and continue past
 * transcripts). Connecting live local agents (Claude Code / Codex) is closed
 * (spec 2026-10-29 P1c).
 */
export default function AgentsHub() {
  return (
    <div className="space-y-12">
      <MyAgentsSection />
    </div>
  );
}
