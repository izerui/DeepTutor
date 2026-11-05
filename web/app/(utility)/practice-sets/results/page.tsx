import { FeaturePage } from "@/components/layout/FeaturePage";
import SetResultsPage from "@/components/practice-sets/SetResultsPage";

/** ``/practice-sets/results?set=…&course=…``: a practice set's results, read only (spec 2026-11-05 one-learner-role §2). */
export default function PracticeSetResultsPage() {
  return <FeaturePage><SetResultsPage /></FeaturePage>;
}
