/** What a first publication sends (spec 2026-11-02 §4.1): nobody is named; people join from the market. */
export interface ScopeUpdate {
  everyone: false;
  /** For the market: nobody receives it until they join (platform batch 1 §4.4). */
  join_only?: boolean;
}
