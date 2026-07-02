import api, { UPLOAD_TIMEOUT } from "./api";
import { streamNdjson, type StreamHandlers } from "./stream";

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

export interface VideoItem {
  id: string;
  display_name: string;
  chapter: string | null;
  topic: string | null;
  subtopic: string | null;
  processing_status: string;
  status: string;
  duration_seconds: number | null;
  is_audio_only: boolean;
  has_slides: boolean;
  processing_job_id: string | null;
  created_at: string;
}

export interface TimelineSegment {
  segment_id: string;
  segment_index: number;
  start_seconds: number;
  end_seconds: number;
  start_time: string;
  end_time: string;
  label: string;
  description: string;
  summary: string;
  topic: string | null;
  subtopic_ids: string[];
  mapping_confidence: number | null;
}

export interface SlideLabel {
  slide_number: number;
  slide_id: string;
  title: string;
  related_timestamps: string[];
  topics: string[];
  summary: string;
}

export interface LectureSummary {
  short_summary: string;
  detailed_summary: string;
  key_points: string[];
  exam_focused_points: string[];
  important_terms: string[];
  possible_questions: {
    mcqs?: { question: string; options: string[]; answer: string }[];
    short?: string[];
    long?: string[];
  };
}

export interface VideoDetail extends VideoItem {
  custom_instruction: string | null;
  media_url: string | null;
  slides_url: string | null;
  summary: LectureSummary | null;
  timeline: TimelineSegment[];
  slides: SlideLabel[];
}

// ── Student ──────────────────────────────────────────────────────────────────────

export interface StudentVideoListItem {
  id: string;
  display_name: string;
  topic: string | null;
  subtopic: string | null;
  duration_seconds: number | null;
  is_audio_only: boolean;
}

export interface StudentPlayerData {
  id: string;
  display_name: string;
  media_url: string | null;
  is_audio_only: boolean;
  duration_seconds: number | null;
  summary: LectureSummary | null;
  timeline: TimelineSegment[];
  slides: SlideLabel[];
}

export interface AskSelectedSegment {
  segment_id: string;
  label: string;
  start_time: string;
  end_time: string;
  start_seconds: number;
}

export interface SupportingKnowledge {
  chunk_id: string;
  topic: string | null;
  subtopic: string | null;
}

export interface AskResponse {
  answer: string;
  language: string;
  chat_session_id: string;
  selected_segments: AskSelectedSegment[];
  detected_topic: string | null;
  detected_subtopic_ids: string[];
  supporting_knowledge_used: SupportingKnowledge[];
  confidence: number;
  follow_up_suggestions: string[];
}

export interface AskBody {
  question: string;
  current_video_time?: string | null;
  chat_session_id?: string | null;
}

export interface ChatHistoryItem {
  id: string;
  question: string;
  answer: string;
  language: string | null;
  selected_segment_ids: string[];
  selected_segments: AskSelectedSegment[];
  detected_topic: string | null;
  confidence: number | null;
  follow_up_suggestions: string[];
  created_at: string;
}

export const videoTutorService = {
  // Admin
  uploadVideo: (form: FormData): Promise<JobRef> =>
    api
      .post("/api/admin/videos", form, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: UPLOAD_TIMEOUT,
      })
      .then((r) => r.data),
  listVideos: (page = 1, perPage = 20): Promise<PagedResult<VideoItem>> =>
    api.get("/api/admin/videos", { params: { page, per_page: perPage } }).then((r) => r.data),
  getVideo: (id: string): Promise<VideoDetail> =>
    api.get(`/api/admin/videos/${id}`).then((r) => r.data),
  retryVideo: (id: string): Promise<JobRef> =>
    api.post(`/api/admin/videos/${id}/retry`).then((r) => r.data),
  activate: (id: string): Promise<VideoItem> =>
    api.post(`/api/admin/videos/${id}/activate`).then((r) => r.data),
  deactivate: (id: string): Promise<VideoItem> =>
    api.post(`/api/admin/videos/${id}/deactivate`).then((r) => r.data),
  deleteVideo: (id: string): Promise<void> =>
    api.delete(`/api/admin/videos/${id}`).then(() => undefined),

  // Student
  listStudentVideos: (): Promise<StudentVideoListItem[]> =>
    api.get("/api/student/videos").then((r) => r.data),
  getPlayerData: (id: string): Promise<StudentPlayerData> =>
    api.get(`/api/student/videos/${id}`).then((r) => r.data),
  ask: (id: string, body: AskBody): Promise<AskResponse> =>
    api.post(`/api/student/videos/${id}/ask`, body, { timeout: 120_000 }).then((r) => r.data),
  askStream: (id: string, body: AskBody, handlers: StreamHandlers, signal?: AbortSignal): Promise<void> =>
    streamNdjson(`/api/student/videos/${id}/ask/stream`, body, handlers, signal),
  getHistory: (id: string): Promise<ChatHistoryItem[]> =>
    api.get(`/api/student/videos/${id}/history`).then((r) => r.data),
};
