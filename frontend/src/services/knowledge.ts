import api, { UPLOAD_TIMEOUT } from "./api";

export interface KnowledgeDocument {
  id: string;
  display_name: string;
  document_type: string;
  exam_id: string;
  chapter: string | null;
  topic: string | null;
  subtopic: string | null;
  processing_status: string;
  chunk_count: number;
  file_id: string;
  created_at: string;
}

export interface KnowledgeDocumentWithJob extends KnowledgeDocument {
  job_id: string;
}

export interface KnowledgeChunk {
  id: string;
  chunk_index: number;
  content: string;
  content_type: string | null;
  chapter: string | null;
  topic: string | null;
  subtopic: string | null;
  language: string | null;
  pinecone_vector_id: string | null;
  quality_status: string | null;
}

export interface Job {
  id: string;
  job_type: string;
  status: string;
  progress_percent: number;
  current_step: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export const knowledgeService = {
  async upload(form: FormData): Promise<KnowledgeDocumentWithJob> {
    const { data } = await api.post<KnowledgeDocumentWithJob>("/api/admin/knowledge/documents", form, {
      headers: { "Content-Type": "multipart/form-data" },
      timeout: UPLOAD_TIMEOUT,
    });
    return data;
  },

  async list(skip = 0, limit = 50): Promise<KnowledgeDocument[]> {
    const { data } = await api.get<KnowledgeDocument[]>("/api/admin/knowledge/documents", {
      params: { skip, limit },
    });
    return data;
  },

  async getChunks(documentId: string): Promise<KnowledgeChunk[]> {
    const { data } = await api.get<KnowledgeChunk[]>(`/api/admin/knowledge/documents/${documentId}/chunks`);
    return data;
  },

  async delete(documentId: string): Promise<void> {
    await api.delete(`/api/admin/knowledge/documents/${documentId}`);
  },

  async reprocess(documentId: string): Promise<Job> {
    const { data } = await api.get<Job>(`/api/admin/knowledge/documents/${documentId}/reprocess`);
    return data;
  },
};
