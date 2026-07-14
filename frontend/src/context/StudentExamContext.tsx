import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { examsService, type Enrollment } from "../services/exams";
import { useAuth } from "./AuthContext";

interface StudentExamContextValue {
  exams: Enrollment[];
  isLoading: boolean;
  selectedExamId: string | null;
  selectedExam: Enrollment | null;
  setSelectedExamId: (id: string) => void;
  refresh: () => Promise<void>;
}

const StudentExamContext = createContext<StudentExamContextValue | null>(null);

const STORAGE_KEY = "student_selected_exam_id";

/**
 * Student-side exam selection, shared across MCQ Tests, Video Tutor, Subjective
 * Tests, and the AI Tutor — picking an exam once (e.g. in the AI Tutor) now
 * scopes every other student page to the same exam, mirroring the admin-side
 * ExamContext. Persisted so it survives reloads.
 */
export function StudentExamProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [exams, setExams] = useState<Enrollment[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedExamId, setSelected] = useState<string | null>(localStorage.getItem(STORAGE_KEY));

  const refresh = useCallback(async () => {
    if (user?.role !== "student") {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const list = await examsService.myExams();
      setExams(list);
      // Keep the persisted selection if still valid; otherwise default to the first exam.
      setSelected((cur) => {
        const valid = cur && list.some((e) => e.exam_id === cur) ? cur : list[0]?.exam_id ?? null;
        if (valid) localStorage.setItem(STORAGE_KEY, valid);
        return valid;
      });
    } finally {
      setIsLoading(false);
    }
  }, [user?.role]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const setSelectedExamId = (id: string) => {
    localStorage.setItem(STORAGE_KEY, id);
    setSelected(id);
  };

  const selectedExam = exams.find((e) => e.exam_id === selectedExamId) ?? null;

  return (
    <StudentExamContext.Provider value={{ exams, isLoading, selectedExamId, selectedExam, setSelectedExamId, refresh }}>
      {children}
    </StudentExamContext.Provider>
  );
}

export function useStudentExam(): StudentExamContextValue {
  const ctx = useContext(StudentExamContext);
  if (!ctx) throw new Error("useStudentExam must be used inside StudentExamProvider");
  return ctx;
}
