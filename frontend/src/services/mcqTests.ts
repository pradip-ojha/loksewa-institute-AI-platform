import api from "./api";
import type { MCQOption, PagedResult } from "./mcq";

// ── Blueprints ────────────────────────────────────────────────────────────────

export interface TopicDistEntry {
  topic?: string | null;
  subtopic?: string | null;
  count: number;
}

export interface DifficultyDist {
  easy: number;
  medium: number;
  hard: number;
}

export interface Blueprint {
  id: string;
  test_name: string;
  total_time_minutes: number;
  num_sets: number;
  topic_distribution: TopicDistEntry[];
  difficulty_distribution: DifficultyDist | null;
  custom_instruction: string | null;
  status: string;
  generation_result: {
    generated?: boolean;
    sets_created?: number;
    reason?: string;
    shortages?: ShortageEntry[];
  } | null;
  job_id: string | null;
  created_at: string;
}

export interface ShortageEntry {
  topic: string | null;
  subtopic: string | null;
  complexity: string | null;
  required: number;
  available: number;
  shortage: number;
}

export interface BlueprintCreatePayload {
  test_name: string;
  total_time_minutes: number;
  num_sets: number;
  topic_distribution: TopicDistEntry[];
  difficulty_distribution?: DifficultyDist | null;
  custom_instruction?: string | null;
}

export interface JobRef {
  id: string;
  status: string;
}

// ── Sets ──────────────────────────────────────────────────────────────────────

export interface TestSet {
  id: string;
  blueprint_id: string;
  set_name: string;
  num_questions: number;
  difficulty_mix: Record<string, number> | null;
  status: string;
  created_at: string;
}

export interface PreviewQuestion {
  id: string;
  question_text: string;
  options: MCQOption[];
  correct_option_ids: string[];
  explanation: string | null;
  topic: string | null;
  subtopic: string | null;
  complexity: string;
  question_order: number;
}

export interface TestSetPreview extends TestSet {
  test_name: string;
  total_time_minutes: number;
  questions: PreviewQuestion[];
}

// ── Student ───────────────────────────────────────────────────────────────────

export type AttemptStatus = "none" | "in_progress" | "submitted";

export interface StudentTestSet {
  set_id: string;
  test_name: string;
  set_name: string;
  total_time_minutes: number;
  num_questions: number;
  attempt_status: AttemptStatus;
  attempt_id: string | null;
  score: number | null;
  correct_count: number | null;
}

export interface AttemptHistoryItem {
  attempt_id: string;
  set_id: string;
  test_name: string;
  set_name: string;
  score: number;
  total_questions: number;
  correct_count: number;
  time_taken_seconds: number | null;
  submitted_at: string | null;
}

export interface TopicPerformance {
  topic: string;
  total: number;
  correct: number;
  accuracy: number;
}

export interface StudentAnalytics {
  total_attempts: number;
  total_questions_answered: number;
  total_correct: number;
  overall_accuracy: number;
  average_score_percent: number;
  best_score_percent: number | null;
  topic_performance: TopicPerformance[];
  weak_topics: TopicPerformance[];
}

export interface StudentQuestion {
  id: string;
  question_text: string;
  options: MCQOption[];
  topic: string | null;
  complexity: string;
  question_order: number;
}

export interface AttemptStart {
  attempt_id: string;
  set_id: string;
  test_name: string;
  total_time_minutes: number;
  started_at: string;
  questions: StudentQuestion[];
}

export interface ResultQuestion {
  id: string;
  question_text: string;
  options: MCQOption[];
  correct_option_ids: string[];
  selected_option_id: string | null;
  is_correct: boolean;
  explanation: string | null;
  topic: string | null;
  complexity: string;
  question_order: number;
}

export interface AttemptResult {
  attempt_id: string;
  set_id: string;
  test_name: string;
  status: string;
  score: number;
  total_questions: number;
  correct_count: number;
  time_taken_seconds: number | null;
  submitted_at: string | null;
  questions: ResultQuestion[];
}

export const mcqTestsService = {
  // Admin
  createBlueprint: (payload: BlueprintCreatePayload): Promise<JobRef> =>
    api.post("/api/admin/mcq-tests/blueprints", payload).then((r) => r.data),
  listBlueprints: (page = 1, perPage = 20): Promise<PagedResult<Blueprint>> =>
    api.get("/api/admin/mcq-tests/blueprints", { params: { page, per_page: perPage } }).then((r) => r.data),
  getBlueprint: (id: string): Promise<Blueprint> =>
    api.get(`/api/admin/mcq-tests/blueprints/${id}`).then((r) => r.data),
  regenerateBlueprint: (id: string): Promise<JobRef> =>
    api.post(`/api/admin/mcq-tests/blueprints/${id}/regenerate`).then((r) => r.data),

  listSets: (params: { blueprint_id?: string; status?: string; page?: number; per_page?: number } = {}): Promise<PagedResult<TestSet>> =>
    api.get("/api/admin/mcq-tests/sets", { params }).then((r) => r.data),
  previewSet: (id: string): Promise<TestSetPreview> =>
    api.get(`/api/admin/mcq-tests/sets/${id}`).then((r) => r.data),
  activateSet: (id: string): Promise<TestSet> =>
    api.post(`/api/admin/mcq-tests/sets/${id}/activate`).then((r) => r.data),
  deactivateSet: (id: string): Promise<TestSet> =>
    api.post(`/api/admin/mcq-tests/sets/${id}/deactivate`).then((r) => r.data),
  archiveSet: (id: string): Promise<TestSet> =>
    api.post(`/api/admin/mcq-tests/sets/${id}/archive`).then((r) => r.data),
  deleteSet: (id: string): Promise<void> =>
    api.delete(`/api/admin/mcq-tests/sets/${id}`).then(() => undefined),

  // Student
  listStudentTests: (): Promise<StudentTestSet[]> =>
    api.get("/api/student/mcq-tests").then((r) => r.data),
  startTest: (setId: string): Promise<AttemptStart> =>
    api.post(`/api/student/mcq-tests/${setId}/start`).then((r) => r.data),
  submitTest: (setId: string, answers: { question_id: string; selected_option_id: string | null }[], timeTakenSeconds: number): Promise<AttemptResult> =>
    api.post(`/api/student/mcq-tests/${setId}/submit`, { answers, time_taken_seconds: timeTakenSeconds }).then((r) => r.data),
  getResult: (attemptId: string): Promise<AttemptResult> =>
    api.get(`/api/student/mcq-tests/attempts/${attemptId}/result`).then((r) => r.data),
  getHistory: (): Promise<AttemptHistoryItem[]> =>
    api.get("/api/student/mcq-tests/history").then((r) => r.data),
  getAnalytics: (): Promise<StudentAnalytics> =>
    api.get("/api/student/mcq-tests/analytics").then((r) => r.data),
};
