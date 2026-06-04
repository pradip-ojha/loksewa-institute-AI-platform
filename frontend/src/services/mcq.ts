import api, { UPLOAD_TIMEOUT } from "./api";

export interface MCQOption {
  id: string;
  label: string;
  text: string;
}

export interface MCQQuestion {
  id: string;
  source_document_id: string | null;
  review_batch_id: string | null;
  origin_type: string;
  question_text: string;
  options: MCQOption[];
  correct_option_ids: string[];
  explanation: string | null;
  chapter: string | null;
  topic: string | null;
  subtopic: string | null;
  complexity: string;
  status: string;
  review_feedback: string | null;
  created_at: string;
  updated_at: string;
}

export interface MCQDocument {
  id: string;
  display_name: string;
  origin_type: string;
  file_id: string | null;
  topic: string | null;
  subtopic: string | null;
  processing_status: string;
  question_count: number;
  created_at: string;
}

export interface MCQReviewBatch {
  id: string;
  document_id: string | null;
  batch_type: string;
  status: string;
  total_questions: number;
  accepted_count: number;
  rejected_count: number;
  rejection_feedback: string | null;
  job_id: string | null;
  created_at: string;
}

export interface MCQBatchWithQuestions extends MCQReviewBatch {
  questions: MCQQuestion[];
}

export interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}

export const mcqService = {
  async uploadDocument(form: FormData): Promise<{ document_id: string; job_id: string }> {
    const { data } = await api.post("/api/admin/mcq/documents/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: UPLOAD_TIMEOUT,
    });
    return data;
  },

  async generateFromContent(form: FormData): Promise<{ document_id: string; job_id: string }> {
    const { data } = await api.post("/api/admin/mcq/generate", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: UPLOAD_TIMEOUT,
    });
    return data;
  },

  async listDocuments(page = 1, perPage = 20): Promise<PagedResult<MCQDocument>> {
    const { data } = await api.get("/api/admin/mcq/documents", { params: { page, per_page: perPage } });
    return data;
  },

  async listBatches(page = 1, perPage = 20): Promise<PagedResult<MCQReviewBatch>> {
    const { data } = await api.get("/api/admin/mcq/review-batches", { params: { page, per_page: perPage } });
    return data;
  },

  async getBatch(batchId: string): Promise<MCQBatchWithQuestions> {
    const { data } = await api.get(`/api/admin/mcq/review-batches/${batchId}`);
    return data;
  },

  async acceptQuestion(batchId: string, questionId: string): Promise<MCQQuestion> {
    const { data } = await api.post(`/api/admin/mcq/review-batches/${batchId}/questions/${questionId}/accept`);
    return data;
  },

  async rejectQuestion(batchId: string, questionId: string, feedback: string): Promise<MCQQuestion> {
    const { data } = await api.post(`/api/admin/mcq/review-batches/${batchId}/questions/${questionId}/reject`, { feedback });
    return data;
  },

  async acceptAll(batchId: string): Promise<{ accepted: number }> {
    const { data } = await api.post(`/api/admin/mcq/review-batches/${batchId}/accept-all`);
    return data;
  },

  async rejectAll(batchId: string, feedback: string): Promise<{ rejected: number }> {
    const { data } = await api.post(`/api/admin/mcq/review-batches/${batchId}/reject-all`, { feedback });
    return data;
  },

  async regenerateBatch(batchId: string, feedback: string): Promise<{ id: string }> {
    const { data } = await api.post(`/api/admin/mcq/review-batches/${batchId}/regenerate`, { feedback });
    return data;
  },

  async listQuestions(params: { status?: string; topic?: string; subtopic?: string; complexity?: string; page?: number; per_page?: number }): Promise<PagedResult<MCQQuestion>> {
    const { data } = await api.get("/api/admin/mcq/questions", { params });
    return data;
  },

  async createQuestion(payload: {
    question_text: string;
    options: MCQOption[];
    correct_option_ids: string[];
    explanation?: string;
    chapter?: string;
    topic?: string;
    subtopic?: string;
    complexity: string;
  }): Promise<MCQQuestion> {
    const { data } = await api.post("/api/admin/mcq/questions", payload);
    return data;
  },

  async updateQuestion(questionId: string, payload: Partial<{
    question_text: string;
    options: MCQOption[];
    correct_option_ids: string[];
    explanation: string;
    chapter: string;
    topic: string;
    subtopic: string;
    complexity: string;
  }>): Promise<MCQQuestion> {
    const { data } = await api.put(`/api/admin/mcq/questions/${questionId}`, payload);
    return data;
  },

  async deleteQuestion(questionId: string): Promise<void> {
    await api.delete(`/api/admin/mcq/questions/${questionId}`);
  },

  async approveQuestion(questionId: string): Promise<MCQQuestion> {
    const { data } = await api.post(`/api/admin/mcq/questions/${questionId}/approve`);
    return data;
  },

  async unapproveQuestion(questionId: string): Promise<MCQQuestion> {
    const { data } = await api.post(`/api/admin/mcq/questions/${questionId}/unapprove`);
    return data;
  },
};
