import { useEffect, useRef, useState } from "react";
import { Sparkles, GraduationCap, Send, BookOpen } from "lucide-react";
import { tutorService } from "../../services/tutor";
import { examsService, type Enrollment } from "../../services/exams";
import { getErrorMessage } from "../../utils/error";
import { PageHeader } from "../../components/ui";
import { RichText } from "../../components/content/RichText";

interface ChatTurn {
  question: string;
  answer?: string;
  mode?: string | null;
  topic?: string | null;
  followUps?: string[];
  loading: boolean;
  error?: string;
}

const STARTER_QUESTIONS = [
  "यो अध्यायको मुख्य अवधारणा बुझाउनुहोस्।",
  "यो विषय परीक्षाको लागि कसरी सोधिन्छ?",
  "महत्त्वपूर्ण शब्दहरू र तिनको अर्थ बताउनुहोस्।",
];

const MODE_LABEL: Record<string, string> = {
  objective: "वस्तुगत",
  subjective: "विषयगत",
  shared: "साझा",
};

export function StudentTutor() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exams, setExams] = useState<Enrollment[]>([]);
  const [examId, setExamId] = useState<string>("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    examsService.myExams().then((list) => {
      setExams(list);
      setExamId((cur) => cur || list[0]?.exam_id || "");
    }).catch(() => {});
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  // Switching exam starts a fresh conversation (sessions are exam-scoped).
  function switchExam(id: string) {
    setExamId(id);
    setSessionId(null);
    setTurns([]);
  }

  async function ask(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    if (!examId) return;
    setQuestion("");
    setBusy(true);
    const idx = turns.length;
    setTurns((t) => [...t, { question: text, loading: true }]);
    try {
      const res = await tutorService.ask({ question: text, exam_id: examId, chat_session_id: sessionId });
      setSessionId(res.chat_session_id);
      setTurns((t) =>
        t.map((turn, i) =>
          i === idx
            ? {
                ...turn,
                loading: false,
                answer: res.answer,
                mode: res.related_mode,
                topic: res.detected_topic,
                followUps: res.follow_up_suggestions,
              }
            : turn,
        ),
      );
    } catch (err) {
      setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, error: getErrorMessage(err, "उत्तर ल्याउन सकिएन।") } : turn)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col pb-20">
      <PageHeader
        title="AI Tutor"
        description="नोट र पुस्तकमा आधारित AI शिक्षकसँग जे पनि सोध्नुहोस्"
        icon={<Sparkles className="h-5 w-5" />}
        actions={
          exams.length > 0 ? (
            <select
              value={examId}
              onChange={(e) => switchExam(e.target.value)}
              className="rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
              title="Exam"
            >
              {exams.map((e) => (
                <option key={e.exam_id} value={e.exam_id}>{e.name}</option>
              ))}
            </select>
          ) : undefined
        }
      />

      {exams.length === 0 && (
        <div className="rounded-xl bg-warning-50 p-4 text-sm text-warning-700 ring-1 ring-warning-100">
          You are not enrolled in any exam yet. Ask your institute to enroll you.
        </div>
      )}

      <div className="flex-1 space-y-4">
        {turns.length === 0 && (
          <div className="rounded-2xl bg-gradient-to-br from-brand-50 to-white p-5 text-center ring-1 ring-brand-100">
            <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-glow">
              <GraduationCap className="h-5 w-5" />
            </div>
            <p className="text-sm font-semibold text-gray-800">AI Tutor लाई सोध्नुहोस्</p>
            <p className="mt-1 text-xs text-gray-500 font-deva">
              उपलब्ध अध्यायबारे जे पनि सोध्नुहोस् — उत्तर नोट र पुस्तकमा आधारित हुन्छ।
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {STARTER_QUESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => ask(s)}
                  className="rounded-full bg-white px-3 py-1.5 text-xs text-brand-700 shadow-sm ring-1 ring-brand-100 transition-colors hover:bg-brand-50 font-deva"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white shadow-sm font-deva">
                {turn.question}
              </div>
            </div>

            <div className="flex items-start gap-2">
              <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
                <GraduationCap className="h-4 w-4" />
              </div>
              <div className="max-w-[88%] rounded-2xl rounded-tl-sm bg-white p-4 shadow-sm ring-1 ring-gray-100">
                {turn.loading && (
                  <div className="flex items-center gap-1 py-1" aria-label="सोच्दै">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.3s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.15s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300" />
                  </div>
                )}
                {turn.error && <p className="text-sm text-danger-600">{turn.error}</p>}
                {turn.answer !== undefined && (
                  <>
                    {turn.topic && (
                      <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-brand-100">
                        <BookOpen className="h-3 w-3" />
                        <span className="font-deva">{turn.topic}</span>
                        {turn.mode && MODE_LABEL[turn.mode] && (
                          <span className="text-brand-400">· {MODE_LABEL[turn.mode]}</span>
                        )}
                      </div>
                    )}
                    <RichText size="sm">{turn.answer}</RichText>

                    {turn.followUps && turn.followUps.length > 0 && (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <p className="mb-1.5 text-xs font-medium text-gray-400">सम्भावित प्रश्न</p>
                        <div className="flex flex-wrap gap-2">
                          {turn.followUps.map((f, j) => (
                            <button
                              key={j}
                              onClick={() => ask(f)}
                              className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200 font-deva"
                            >
                              {f}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); void ask(question); }}
        className="sticky bottom-16 mt-4 flex items-center gap-2 rounded-full bg-white p-1.5 shadow-md ring-1 ring-gray-200"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="प्रश्न सोध्नुहोस्…"
          className="flex-1 rounded-full border-0 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-0 font-deva"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:opacity-40"
          aria-label="Send"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>
    </div>
  );
}
