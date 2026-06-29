import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { examsService, type Exam } from "../services/exams";
import { useAuth } from "./AuthContext";

interface ExamContextValue {
  exams: Exam[];
  isLoading: boolean;
  selectedExamId: string | null;
  selectedExam: Exam | null;
  setSelectedExamId: (id: string) => void;
  refresh: () => Promise<void>;
}

const ExamContext = createContext<ExamContextValue | null>(null);

const STORAGE_KEY = "admin_selected_exam_id";

/**
 * Admin-side exam selection. `exam_id` is the universal scoping key, so every admin
 * workspace (knowledge, MCQ, tests, subjective, video, syllabus) operates within the
 * selected exam. The choice is persisted so it survives reloads.
 */
export function ExamProvider({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const [exams, setExams] = useState<Exam[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedExamId, setSelected] = useState<string | null>(localStorage.getItem(STORAGE_KEY));

  const refresh = useCallback(async () => {
    if (user?.role !== "institute_admin") {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    try {
      const list = await examsService.list();
      setExams(list);
      // Keep the persisted selection if still valid; otherwise default to the first exam.
      setSelected((cur) => {
        const valid = cur && list.some((e) => e.id === cur) ? cur : list[0]?.id ?? null;
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

  const selectedExam = exams.find((e) => e.id === selectedExamId) ?? null;

  return (
    <ExamContext.Provider value={{ exams, isLoading, selectedExamId, selectedExam, setSelectedExamId, refresh }}>
      {children}
    </ExamContext.Provider>
  );
}

export function useExam(): ExamContextValue {
  const ctx = useContext(ExamContext);
  if (!ctx) throw new Error("useExam must be used inside ExamProvider");
  return ctx;
}
