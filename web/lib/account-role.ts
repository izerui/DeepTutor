/**
 * Account roles (spec 2026-11-05): an administrator or a learner. Authoring is
 * not a role — whoever made an item is its author.
 */
export type AccountRole = "admin" | "learner";

/** The label key for a role; anything but ``admin`` reads as a learner. */
export function accountRoleLabelKey(role: string): "Administrator" | "Learner" {
  return role === "admin" ? "Administrator" : "Learner";
}
