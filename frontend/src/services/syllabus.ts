import api from "./api";

export interface SubtopicEntry {
  id: string;
  subtopic: string;
}

export interface TopicNode {
  topic: string;
  subtopics: SubtopicEntry[];
}

export interface ChapterNode {
  chapter: string;
  topics: TopicNode[];
}

export interface SyllabusTree {
  exam_id: string;
  chapters: ChapterNode[];
}

const base = (examId: string) => `/api/admin/syllabus/exams/${examId}`;

export const syllabusService = {
  get: (examId: string): Promise<SyllabusTree> => api.get(base(examId)).then((r) => r.data),

  addItem: (examId: string, chapter: string, topic: string, subtopic?: string): Promise<SyllabusTree> =>
    api.post(`${base(examId)}/items`, { chapter, topic, subtopic }).then((r) => r.data),

  deleteItem: (itemId: string): Promise<SyllabusTree> =>
    api.delete(`/api/admin/syllabus/items/${itemId}`).then((r) => r.data),

  updateItem: (itemId: string, fields: { chapter?: string; topic?: string; subtopic?: string }): Promise<SyllabusTree> =>
    api.put(`/api/admin/syllabus/items/${itemId}`, fields).then((r) => r.data),

  renameChapter: (examId: string, old_chapter: string, new_chapter: string): Promise<SyllabusTree> =>
    api.put(`${base(examId)}/chapter`, { old_chapter, new_chapter }).then((r) => r.data),

  deleteChapter: (examId: string, chapter: string): Promise<SyllabusTree> =>
    api.delete(`${base(examId)}/chapter`, { data: { chapter } }).then((r) => r.data),

  renameTopic: (examId: string, chapter: string, old_topic: string, new_topic: string): Promise<SyllabusTree> =>
    api.put(`${base(examId)}/topic`, { chapter, old_topic, new_topic }).then((r) => r.data),

  deleteTopic: (examId: string, chapter: string, topic: string): Promise<SyllabusTree> =>
    api.delete(`${base(examId)}/topic`, { data: { chapter, topic } }).then((r) => r.data),
};
