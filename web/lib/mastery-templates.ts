/**
 * Derives the freshest version of the template the user opened a dialog for.
 *
 * `managing` is the snapshot taken when the dialog was opened. If the parent
 * list has since been reloaded (archive, new revision, …), the match from the
 * fresh list carries the update so the dialog sees it without a second piece of
 * state.
 */
export function currentTemplate<T extends { id: string }>(
  templates: readonly T[],
  managing: T,
): T {
  return templates.find((t) => t.id === managing.id) ?? managing;
}
