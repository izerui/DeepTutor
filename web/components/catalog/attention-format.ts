import type { AttentionFlag } from "@/lib/report-types";

type Translate = (key: string, values?: Record<string, unknown>) => string;

/** One short line per reason a student needs attention (spec 2026-11-03 batch 3 §5.3). */
export function flagText(flag: AttentionFlag, t: Translate): string {
  switch (flag.kind) {
    case "inactive":
      return flag.days == null ? t("Has not learned yet") : t("No learning for {{days}} days", { days: flag.days });
    case "overdue":
      return t("{{count}} tasks overdue", { count: flag.count });
    case "mistakes_rising":
      return t("{{recent}} wrong answers this week ({{before}} the week before)", { recent: flag.recent, before: flag.before });
    case "stuck":
      return t("Stuck on “{{name}}” for {{days}} days, {{wrong}} wrong", { name: flag.name, days: flag.days, wrong: flag.wrong });
    default:
      return "";
  }
}
