/** Learning-report shapes shared by the course / Mastery Path reports and the workbench. */

export interface AttemptTally {
  correct: number;
  partial: number;
  incorrect: number;
}

export interface TeacherPathReport {
  template_id: string;
  title: string;
  revision: number;
  kp_total: number;
  kp_mastered: number;
  average_mastery: number;
  attempts: AttemptTally;
  last_activity: number | null;
  knowledge_points?: {
    id: string;
    name: string;
    mastery: number;
    mastered: boolean;
    /** Batch 3 §6.2: answers and wrong answers on this point, and days stuck (not mastered). */
    answers?: number;
    wrong?: number;
    first_answered_at?: number | null;
    last_answered_at?: number | null;
    stuck_days?: number | null;
  }[];
}

export interface OverallReport {
  path_count: number;
  self_study_path_count: number;
  kp_total: number;
  kp_mastered: number;
  attempts: AttemptTally;
  last_activity: number | null;
  question_bank: { total: number; correct: number; partial: number; incorrect: number };
  study_minutes_total: number;
  study_minutes_7d: number;
  /** Batch 3 §6.3: the chosen period (``null``: all time) and the last 8 weeks. */
  period_days?: number | null;
  period_minutes?: number;
  period_answers?: number;
  trend?: { minutes: number; answers: number }[];
}

export interface OverallReport {
  path_count: number;
  self_study_path_count: number;
  kp_total: number;
  kp_mastered: number;
  attempts: AttemptTally;
  last_activity: number | null;
  question_bank: { total: number; correct: number; partial: number; incorrect: number };
  study_minutes_total: number;
  study_minutes_7d: number;
  /** Batch 3 §6.3: the chosen period (``null``: all time) and the last 8 weeks. */
  period_days?: number | null;
  period_minutes?: number;
  period_answers?: number;
  trend?: { minutes: number; answers: number }[];
}

export interface PracticeSetResult {
  set_id: string;
  title: string;
  attempts: number;
  first_score: number;
  best_score: number;
  /** The maximum of the version the best score was on. */
  best_max_score: number;
  max_score: number;
  last_submitted_at: number;
}
