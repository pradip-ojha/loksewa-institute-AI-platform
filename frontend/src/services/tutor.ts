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
  related_mode: string | null;
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

export interface TutorHistoryItem {
  id: string;
  question: string;
  answer: string;
  language: string | null;
  related_mode: string | null;
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
};
