import { FeaturePage } from "@/components/layout/FeaturePage";
import LearnerReportPage from "@/components/catalog/LearnerReport";

/** Platform batch 3 §1: ``/reports?kind=course|mastery_path&id=…``. */
export default function ReportsPage() {
  return <FeaturePage><LearnerReportPage /></FeaturePage>;
}
