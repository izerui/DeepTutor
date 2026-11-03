export type GrantPayload = {
  version: number;
  user_id: string;
  models: {
    llm: Array<Record<string, unknown>>;
  };
  /** Retired: knowledge bases are shared by making them public. Kept so stored grants round-trip. */
  knowledge_bases: Array<Record<string, unknown>>;
  skills: Array<Record<string, unknown>>;
  /** Admin-assigned partners the user may see & consult ([{ partner_id }]). */
  partners: Array<Record<string, unknown>>;
  /** null = default (all system tools), [] = none, array = whitelist. */
  enabled_tools: string[] | null;
  /** null = default (all MCP tools), [] = none, array = whitelist. */
  mcp_tools: string[] | null;
  /** null = follow deployment exec policy, false = always disabled. */
  exec_enabled: boolean | null;
  learning_policy: LearningPolicy | null;
  /** Student-only abilities; null = defaults (personal mastery on, private KB off). */
  student_policy: StudentPolicy | null;
};

export type LearningPolicy = {
  age_band: "6-8" | "9-12" | "13-15";
  locked_persona: "teacher";
  allowed_capabilities: Array<"chat" | "immersive_reading" | "mastery_path">;
  default_capability: "chat" | "immersive_reading" | "mastery_path";
  allowed_surfaces: Array<"chat" | "reading">;
  reading: {
    allow_upload: boolean;
    material_ids: string[];
    extensions: string[];
  };
};

/**
 * What is set for one student; a missing key follows their classes, then the
 * default (spec 2026-10-29 §6; batch 3 §7). ``ai_daily_tokens`` 0 is no limit.
 */
export type StudentPolicy = {
  allow_personal_mastery?: boolean;
  allow_personal_book?: boolean;
  allow_private_kb?: boolean;
  allow_personal_course?: boolean;
  allow_personal_task_card?: boolean;
  allow_partners?: boolean;
  allow_agents?: boolean;
  allow_co_writer?: boolean;
  ai_daily_tokens?: number;
};

export const STUDENT_POLICY_DEFAULTS: Required<Omit<StudentPolicy, "ai_daily_tokens">> = {
  allow_personal_mastery: true,
  allow_personal_book: true,
  allow_private_kb: false,
  allow_personal_course: true,
  allow_personal_task_card: true,
  allow_partners: false,
  allow_agents: false,
  allow_co_writer: false,
};

export type ToolOption = { name: string; description?: string };

export type McpToolOption = {
  name: string;
  /** Provider grouping key; `server` is its pre-provider spelling. */
  provider_id?: string;
  server?: string;
  /** `"mcp"` today, `"cli"` once CLI-app providers land. */
  kind?: string;
  description?: string;
};

export type MultiUserResources = {
  models: {
    llm: Array<{
      profile_id: string;
      name: string;
      models?: Array<{ model_id: string; name: string; model?: string }>;
    }>;
  };
  skills: Array<{ name: string; description?: string; tags?: string[] }>;
  partners: Array<{ partner_id: string; name: string; description?: string }>;
  reading_materials: Array<{
    material_id: string;
    title: string;
    filename: string;
    render_mode: string;
  }>;
  reading_extensions: Array<{
    id: string;
    name: string;
    version: string;
  }>;
  tools: ToolOption[];
  mcp_tools: McpToolOption[];
};

/** Per-book levels are retired; creation remains an account permission. */
export type BookPermission = {
  create: boolean;
};

/** One line of the admin migration results notice; rendered by kind. */
export type MigrationItem = {
  kind: string;
  key: string;
  data: Record<string, unknown>;
  created_at: string;
};
