import api from "./api";

// ── MCQ ──────────────────────────────────────────────────────────────────────
export interface MCQSummary {
  total_attempts: number;
  total_students: number;
  average_score_percent: number;
  highest_score_percent: number;
  lowest_score_percent: number;
}
export interface MCQStudentResult {
  student_id: string;
  student_name: string;
  student_email: string;
  attempts: number;
  average_percent: number;
  best_percent: number;
}
export interface TopicPerf {
  topic: string;
  total: number;
  correct: number;
  accuracy: number;
}
export interface SubtopicPerf extends TopicPerf {
  subtopic: string;
}
export interface QuestionPerf {
  question_id: string;
  question_text: string;
  topic: string;
  subtopic: string | null;
  times_answered: number;
  correct: number;
  accuracy: number;
}
export interface MCQOverview {
  summary: MCQSummary;
  student_results: MCQStudentResult[];
  topic_performance: TopicPerf[];
  weak_subtopics: SubtopicPerf[];
  hardest_questions: QuestionPerf[];
}

// ── Subjective ───────────────────────────────────────────────────────────────
export interface SubjectiveSummary {
  total_submissions: number;
  checked_submissions: number;
  total_students: number;
  average_percent: number;
  highest_percent: number;
  lowest_percent: number;
  low_confidence_count: number;
  checked_pdf_count: number;
}
export interface SubjectiveTestBreakdown {
  test_id: string;
  display_name: string;
  total_marks: number | null;
  submissions: number;
  average_percent: number;
  average_marks: number;
  highest_marks: number;
  lowest_marks: number;
}
export interface MistakeItem {
  text: string;
  count: number;
}
export interface SubjectiveOverview {
  summary: SubjectiveSummary;
  test_breakdown: SubjectiveTestBreakdown[];
  common_mistakes: MistakeItem[];
}
export interface SubjectiveStudentResult {
  student_id: string;
  student_name: string;
  student_email: string;
  marks_awarded: number;
  marks_possible: number;
  percent: number;
  confidence: number | null;
}
export interface SubjectiveQuestionPerf {
  question_number: string;
  submissions: number;
  average_awarded: number;
  average_max: number;
  accuracy: number;
}
export interface SubjectiveTestDetail {
  test_id: string;
  display_name: string;
  submissions: number;
  student_results: SubjectiveStudentResult[];
  question_performance: SubjectiveQuestionPerf[];
  common_mistakes: MistakeItem[];
}

// ── Video ────────────────────────────────────────────────────────────────────
export interface VideoOverviewItem {
  video_id: string;
  display_name: string;
  processing_status: string;
  status: string;
  total_views: number;
  unique_viewers: number;
  total_questions: number;
  low_confidence_answers: number;
}
export interface AskedQuestion {
  question: string;
  count: number;
}
export interface UnclearConcept {
  topic: string;
  count: number;
}
export interface VideoStudentQuestions {
  student_id: string;
  student_name: string;
  student_email: string;
  questions: number;
}
export interface LowConfidenceAnswer {
  question: string;
  confidence: number;
  detected_topic: string | null;
  created_at: string;
}
export interface VideoDetail {
  video_id: string;
  display_name: string;
  total_views: number;
  unique_viewers: number;
  total_questions: number;
  most_asked_questions: AskedQuestion[];
  unclear_concepts: UnclearConcept[];
  student_questions: VideoStudentQuestions[];
  low_confidence_answers: LowConfidenceAnswer[];
}

export const analyticsService = {
  mcqOverview: (): Promise<MCQOverview> =>
    api.get("/api/admin/analytics/mcq/overview").then((r) => r.data),

  subjectiveOverview: (): Promise<SubjectiveOverview> =>
    api.get("/api/admin/analytics/subjective/overview").then((r) => r.data),

  subjectiveTestDetail: (testId: string): Promise<SubjectiveTestDetail> =>
    api.get(`/api/admin/analytics/subjective/tests/${testId}`).then((r) => r.data),

  videoOverview: (): Promise<VideoOverviewItem[]> =>
    api.get("/api/admin/analytics/video").then((r) => r.data),

  videoDetail: (videoId: string): Promise<VideoDetail> =>
    api.get(`/api/admin/analytics/video/${videoId}`).then((r) => r.data),
};
