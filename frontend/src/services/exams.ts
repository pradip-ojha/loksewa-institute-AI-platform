import api from "./api";

export type ExamType = "objective" | "subjective";

export interface Exam {
  id: string;
  exam_type: ExamType;
  name: string;
  description: string | null;
  status: string; // active | archived
  created_at: string;
}

export interface Enrollment {
  exam_id: string;
  exam_type: ExamType;
  name: string;
  enrolled_at: string;
}

export const examsService = {
  list: (examType?: ExamType): Promise<Exam[]> =>
    api.get("/api/admin/exams", { params: examType ? { exam_type: examType } : {} }).then((r) => r.data),

  create: (payload: { exam_type: ExamType; name: string; description?: string }): Promise<Exam> =>
    api.post("/api/admin/exams", payload).then((r) => r.data),

  update: (examId: string, payload: { name?: string; description?: string; status?: string }): Promise<Exam> =>
    api.put(`/api/admin/exams/${examId}`, payload).then((r) => r.data),

  // Student enrollment (admin-managed, under the Students UI)
  listStudentExams: (studentId: string): Promise<Enrollment[]> =>
    api.get(`/api/admin/students/${studentId}/exams`).then((r) => r.data),

  enroll: (studentId: string, examId: string): Promise<void> =>
    api.post(`/api/admin/students/${studentId}/exams`, { exam_id: examId }).then(() => undefined),

  unenroll: (studentId: string, examId: string): Promise<void> =>
    api.delete(`/api/admin/students/${studentId}/exams/${examId}`).then(() => undefined),

  // Student-facing: the exams the logged-in student is enrolled in
  myExams: (): Promise<Enrollment[]> =>
    api.get("/api/student/exams").then((r) => r.data),
};
