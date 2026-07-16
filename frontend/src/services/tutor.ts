import api from "./api";
import { streamNdjson, type StreamHandlers } from "./stream";

export interface SupportingKnowledge {
  chunk_id: string;
  topic: string | null;
  subtopic: string | null;
}

export interface TutorAskResponse {
  answer: string;
  language: string;
  chat_session_id: string;
  detected_topic: string | null;
  detected_subtopic_ids: string[];
  supporting_knowledge_used: SupportingKnowledge[];
  confidence: number;
  selection_confidence: number;
  follow_up_suggestions: string[];
}

export interface TutorAskBody {
  question: string;
  exam_id?: string | null;       // required to START a new chat; omitted when resuming
  chat_session_id?: string | null;
}

export interface TutorSessionItem {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  last_message_at: string;
}

export interface TutorHistoryItem {
  id: string;
  session_id: string;
  question: string;
  answer: string;
  language: string | null;
  detected_topic: string | null;
  follow_up_suggestions: string[];
  created_at: string;
}

export const tutorService = {
  ask: (body: TutorAskBody): Promise<TutorAskResponse> =>
    api.post("/api/student/tutor/ask", body, { timeout: 120_000 }).then((r) => r.data),
  askStream: (body: TutorAskBody, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> =>
    streamNdjson("/api/student/tutor/ask/stream", body, handlers, signal),
  getHistory: (sessionId: string): Promise<TutorHistoryItem[]> =>
    api.get("/api/student/tutor/history", { params: { session_id: sessionId } }).then((r) => r.data),
  // ChatGPT-style session sidebar: list sessions for an exam / delete one.
  getSessions: (examId: string): Promise<TutorSessionItem[]> =>
    api.get("/api/student/tutor/sessions", { params: { exam_id: examId } }).then((r) => r.data),
  deleteSession: (sessionId: string): Promise<void> =>
    api.delete(`/api/student/tutor/sessions/${sessionId}`).then(() => undefined),
  // Full conversation for an exam, merged across sessions — used to restore the
  // chat when the AI Tutor page is reopened.
  getHistoryByExam: (examId: string): Promise<TutorHistoryItem[]> =>
    api.get("/api/student/tutor/history", { params: { exam_id: examId } }).then((r) => r.data),
};
