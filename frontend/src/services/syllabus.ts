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
  syllabus_type: string;
  chapters: ChapterNode[];
}

const base = (type: string) => `/api/admin/syllabus/${type}`;

export const syllabusService = {
  getObjective: (): Promise<SyllabusTree> => api.get(base("objective")).then(r => r.data),
  getSubjective: (): Promise<SyllabusTree> => api.get(base("subjective")).then(r => r.data),

  addItem: (type: string, chapter: string, topic: string, subtopic?: string): Promise<SyllabusTree> =>
    api.post(`${base(type)}/items`, { chapter, topic, subtopic }).then(r => r.data),

  deleteItem: (itemId: string): Promise<SyllabusTree> =>
    api.delete(`/api/admin/syllabus/items/${itemId}`).then(r => r.data),

  updateItem: (itemId: string, fields: { chapter?: string; topic?: string; subtopic?: string }): Promise<SyllabusTree> =>
    api.put(`/api/admin/syllabus/items/${itemId}`, fields).then(r => r.data),

  renameChapter: (type: string, old_chapter: string, new_chapter: string): Promise<SyllabusTree> =>
    api.put(`${base(type)}/chapter`, { old_chapter, new_chapter }).then(r => r.data),

  deleteChapter: (type: string, chapter: string): Promise<SyllabusTree> =>
    api.delete(`${base(type)}/chapter`, { data: { chapter } }).then(r => r.data),

  renameTopic: (type: string, chapter: string, old_topic: string, new_topic: string): Promise<SyllabusTree> =>
    api.put(`${base(type)}/topic`, { chapter, old_topic, new_topic }).then(r => r.data),

  deleteTopic: (type: string, chapter: string, topic: string): Promise<SyllabusTree> =>
    api.delete(`${base(type)}/topic`, { data: { chapter, topic } }).then(r => r.data),
};
