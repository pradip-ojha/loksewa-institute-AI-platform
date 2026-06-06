import api, { UPLOAD_TIMEOUT } from "./api";

export interface JobRef {
  id: string;
  status: string;
}

export interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

// ── Admin ───────────────────────────────────────────────────────────────────────

export interface SubjectiveTest {
  id: string;
  display_name: string;
  total_time_minutes: number;
  num_questions: number;
  total_marks: number;
  status: string;
  skill_generation_status: string;
  skill_generation_job_id: string | null;
  has_rubric: boolean;
  created_at: string;
}

export interface SubjectiveQuestion {
  id: string;
  question_number: string;
  question_text: string;
  marks: number;
  question_order: number;
  topic: string | null;
  subtopic: string | null;
}

export interface SubjectiveTestDetail extends SubjectiveTest {
  custom_instruction: string | null;
  question_paper_url: string | null;
  model_answer_url: string | null;
  rubric_url: string | null;
  questions: SubjectiveQuestion[];
}

export interface Submission {
  sheet_id: string;
  student_id: string;
  student_name: string;
  student_email: string;
  upload_attempt_number: number;
  current_status: string;
  total_marks_awarded: number | null;
  total_marks_possible: number | null;
  checked_pdf_url: string | null;
  created_at: string;
}

// ── Student ───────────────────────────────────────────────────────────────────

export interface StudentTestListItem {
  test_id: string;
  display_name: string;
  total_time_minutes: number;
  num_questions: number;
  total_marks: number;
  submission_status: string;
  sheet_id: string | null;
  upload_attempt_number: number;
  total_marks_awarded: number | null;
}

export interface StudentTestDetail {
  test_id: string;
  display_name: string;
  total_time_minutes: number;
  num_questions: number;
  total_marks: number;
  question_paper_url: string | null;
  submission_status: string;
  sheet_id: string | null;
  upload_attempt_number: number;
}

export interface QualityResult {
  overall_status: string;
  readability_score: number | null;
  quality_notes: string | null;
}

export interface ResultQuestion {
  question_number: string;
  question_text: string;
  marks_awarded: number;
  marks_possible: number;
  feedback: string | null;
  mistakes: string[];
}

export interface AnswerResult {
  sheet_id: string;
  test_id: string;
  display_name: string;
  status: string;
  upload_attempt_number: number;
  can_reupload: boolean;
  total_marks_awarded: number | null;
  total_marks_possible: number | null;
  quality: QualityResult | null;
  questions: ResultQuestion[];
  checked_pdf_url: string | null;
}

export const subjectiveTestsService = {
  // Admin
  createTest: (form: FormData): Promise<JobRef> =>
    api
      .post("/api/admin/subjective/tests", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: UPLOAD_TIMEOUT,
      })
      .then((r) => r.data),
  listTests: (page = 1, perPage = 20): Promise<PagedResult<SubjectiveTest>> =>
    api.get("/api/admin/subjective/tests", { params: { page, per_page: perPage } }).then((r) => r.data),
  getTest: (id: string): Promise<SubjectiveTestDetail> =>
    api.get(`/api/admin/subjective/tests/${id}`).then((r) => r.data),
  activateTest: (id: string): Promise<SubjectiveTest> =>
    api.post(`/api/admin/subjective/tests/${id}/activate`).then((r) => r.data),
  archiveTest: (id: string): Promise<SubjectiveTest> =>
    api.post(`/api/admin/subjective/tests/${id}/archive`).then((r) => r.data),
  regenerateSkills: (id: string): Promise<JobRef> =>
    api.post(`/api/admin/subjective/tests/${id}/regenerate-skills`).then((r) => r.data),
  deleteTest: (id: string): Promise<void> =>
    api.delete(`/api/admin/subjective/tests/${id}`).then(() => undefined),
  listSubmissions: (id: string): Promise<Submission[]> =>
    api.get(`/api/admin/subjective/tests/${id}/submissions`).then((r) => r.data),

  // Student
  listStudentTests: (): Promise<StudentTestListItem[]> =>
    api.get("/api/student/subjective/tests").then((r) => r.data),
  getStudentTest: (id: string): Promise<StudentTestDetail> =>
    api.get(`/api/student/subjective/tests/${id}`).then((r) => r.data),
  uploadAnswer: (testId: string, form: FormData): Promise<JobRef> =>
    api
      .post(`/api/student/subjective/tests/${testId}/upload-answer`, form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: UPLOAD_TIMEOUT,
      })
      .then((r) => r.data),
  getResult: (testId: string): Promise<AnswerResult> =>
    api.get(`/api/student/subjective/tests/${testId}/result`).then((r) => r.data),
};
