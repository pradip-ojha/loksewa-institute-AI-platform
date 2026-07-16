import { useEffect, useRef, useState } from "react";
import {
  Sparkles, GraduationCap, Send, BookOpen, Plus, Trash2,
  PanelLeftClose, PanelLeftOpen, MessageSquare,
} from "lucide-react";
import { tutorService, type TutorSessionItem } from "../../services/tutor";
import { useStudentExam } from "../../context/StudentExamContext";
import { PageHeader } from "../../components/ui";
import { RichText } from "../../components/content/RichText";

interface ChatTurn {
  question: string;
  answer?: string;
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

const isDesktop = () => typeof window !== "undefined" && window.innerWidth >= 1024;

export function StudentTutor() {
  const { exams, selectedExamId: examId, isLoading: examsLoading } = useStudentExam();
  const [sessions, setSessions] = useState<TutorSessionItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  // null = a fresh "new chat" — the backend creates the session on the first ask.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(isDesktop);
  const endRef = useRef<HTMLDivElement>(null);
  // Tracks the in-flight stream so we can abort it (unmount / exam switch / session
  // switch), stopping backend token generation and avoiding setState-after-unmount.
  const abortRef = useRef<AbortController | null>(null);

  // Abort any in-flight stream when the page unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  const mapHistory = (items: Awaited<ReturnType<typeof tutorService.getHistory>>): ChatTurn[] =>
    items.map((m) => ({
      question: m.question,
      answer: m.answer,
      topic: m.detected_topic,
      followUps: m.follow_up_suggestions,
      loading: false,
    }));

  // Load this exam's session list (ChatGPT-style sidebar) and open the most recent
  // session so reopening AI Tutor resumes instead of starting blank.
  useEffect(() => {
    abortRef.current?.abort();
    setTurns([]);
    setSessionId(null);
    setSessions([]);
    setHistoryLoaded(false);
    if (!examId) {
      setHistoryLoaded(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const list = await tutorService.getSessions(examId);
        if (cancelled) return;
        setSessions(list);
        if (list.length > 0) {
          setSessionId(list[0].id);
          const items = await tutorService.getHistory(list[0].id);
          if (cancelled) return;
          setTurns(mapHistory(items));
        }
      } catch {
        /* session list is best-effort; a failure just starts an empty chat */
      } finally {
        if (!cancelled) setHistoryLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [examId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  async function refreshSessions() {
    if (!examId) return;
    try {
      setSessions(await tutorService.getSessions(examId));
    } catch {
      /* keep the current list on failure */
    }
  }

  async function openSession(id: string) {
    if (busy) abortRef.current?.abort();
    setSessionId(id);
    setTurns([]);
    setHistoryLoaded(false);
    if (!isDesktop()) setSidebarOpen(false);
    try {
      setTurns(mapHistory(await tutorService.getHistory(id)));
    } catch {
      /* start empty; asking still works against the session */
    } finally {
      setHistoryLoaded(true);
    }
  }

  function newChat() {
    if (busy) abortRef.current?.abort();
    setSessionId(null);
    setTurns([]);
    setHistoryLoaded(true);
    if (!isDesktop()) setSidebarOpen(false);
  }

  async function deleteSession(id: string) {
    if (!window.confirm("यो कुराकानी मेटाउने? यसका सबै प्रश्नोत्तर हट्नेछन्।")) return;
    try {
      await tutorService.deleteSession(id);
    } catch {
      return; // deletion failed — leave the list untouched
    }
    const remaining = sessions.filter((s) => s.id !== id);
    setSessions(remaining);
    if (sessionId === id) {
      if (remaining.length > 0) void openSession(remaining[0].id);
      else newChat();
    }
  }

  async function ask(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    if (!examId) return;
    setQuestion("");
    setBusy(true);
    const idx = turns.length;
    setTurns((t) => [...t, { question: text, loading: true }]);
    const controller = new AbortController();
    abortRef.current = controller;
    let streamed = false;
    let failedMsg: string | null = null;
    try {
    await tutorService.askStream(
      { question: text, exam_id: examId, chat_session_id: sessionId },
      {
        onMeta: (meta) => {
          setSessionId(meta.chat_session_id);
          setTurns((t) =>
            t.map((turn, i) =>
              i === idx ? { ...turn, topic: (meta.detected_topic as string | null) ?? null } : turn,
            ),
          );
        },
        onDelta: (delta) => {
          // First delta clears the loading dots; subsequent deltas append live.
          streamed = true;
          setTurns((t) =>
            t.map((turn, i) =>
              i === idx ? { ...turn, loading: false, answer: (turn.answer ?? "") + delta } : turn,
            ),
          );
        },
        onDone: (done) => {
          setTurns((t) =>
            t.map((turn, i) =>
              i === idx
                ? { ...turn, loading: false, answer: turn.answer ?? "", followUps: done.follow_up_suggestions ?? [] }
                : turn,
            ),
          );
        },
        onError: (message) => {
          failedMsg = message;
        },
      },
      controller.signal,
    );
    // Stream failed before any answer text arrived → retry once via the plain
    // (non-stream) endpoint before surfacing the error.
    if (failedMsg !== null) {
      if (!streamed && !controller.signal.aborted) {
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
                    topic: res.detected_topic,
                    followUps: res.follow_up_suggestions ?? [],
                  }
                : turn,
            ),
          );
          failedMsg = null;
        } catch {
          /* fall through to the stream's error message */
        }
      }
      if (failedMsg !== null) {
        const message = failedMsg;
        setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, error: message } : turn)));
      }
    }
    // Keep the sidebar current (new session appears / active one bumps to top).
    if (!controller.signal.aborted) void refreshSessions();
    } finally {
      // Always re-enable input, even if a handler threw or the stream rejected.
      if (abortRef.current === controller) abortRef.current = null;
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
          <div className="flex items-center gap-2">
            <button
              onClick={() => setSidebarOpen((v) => !v)}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-gray-600 ring-1 ring-gray-200 transition-colors hover:bg-gray-50"
              aria-label={sidebarOpen ? "Hide chat list" : "Show chat list"}
              title="कुराकानी सूची"
            >
              {sidebarOpen ? <PanelLeftClose className="h-4 w-4" /> : <PanelLeftOpen className="h-4 w-4" />}
            </button>
            <button
              onClick={newChat}
              className="flex h-9 items-center gap-1.5 rounded-lg bg-brand-600 px-3 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
            >
              <Plus className="h-4 w-4" /> नयाँ च्याट
            </button>
          </div>
        }
      />

      {!examsLoading && exams.length === 0 && (
        <div className="rounded-xl bg-warning-50 p-4 text-sm text-warning-700 ring-1 ring-warning-100">
          You are not enrolled in any exam yet. Ask your institute to enroll you.
        </div>
      )}

      <div className="relative flex flex-1 items-stretch gap-4">
        {/* ── Session sidebar (ChatGPT-style; overlay drawer on mobile) ─────────── */}
        {sidebarOpen && (
          <>
            <div
              className="fixed inset-0 z-30 bg-black/30 lg:hidden"
              onClick={() => setSidebarOpen(false)}
            />
            <aside className="fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-white p-3 shadow-xl lg:static lg:z-auto lg:w-64 lg:flex-shrink-0 lg:rounded-lg lg:border lg:border-gray-200 lg:shadow-none">
              <div className="mb-2 flex items-center justify-between px-1">
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">कुराकानीहरू</p>
                <button
                  onClick={newChat}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-brand-600 transition-colors hover:bg-brand-50"
                >
                  <Plus className="h-3.5 w-3.5" /> नयाँ
                </button>
              </div>
              <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
                {sessions.length === 0 && (
                  <p className="px-2 py-6 text-center text-xs text-gray-400 font-deva">
                    अहिलेसम्म कुनै कुराकानी छैन।
                  </p>
                )}
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className={`group flex items-center gap-1 rounded-lg pr-1 transition-colors ${
                      s.id === sessionId ? "bg-brand-50 ring-1 ring-brand-100" : "hover:bg-gray-50"
                    }`}
                  >
                    <button
                      onClick={() => void openSession(s.id)}
                      className="min-w-0 flex-1 px-2.5 py-2 text-left"
                    >
                      <span className="flex items-center gap-1.5">
                        <MessageSquare className={`h-3.5 w-3.5 flex-shrink-0 ${s.id === sessionId ? "text-brand-500" : "text-gray-300"}`} />
                        <span className="truncate text-sm text-gray-800 font-deva">{s.title}</span>
                      </span>
                      <span className="mt-0.5 block pl-5 text-[11px] text-gray-400">
                        {new Date(s.last_message_at).toLocaleDateString()} · {s.message_count} प्रश्न
                      </span>
                    </button>
                    <button
                      onClick={() => void deleteSession(s.id)}
                      className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-300 opacity-0 transition-all hover:bg-danger-50 hover:text-danger-600 focus:opacity-100 group-hover:opacity-100"
                      aria-label="Delete chat"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </aside>
          </>
        )}

        {/* ── Active conversation ────────────────────────────────────────────────── */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-4">
            {historyLoaded && turns.length === 0 && (
              <div className="rounded-lg border border-gray-200 bg-white p-5 text-center">
                <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-brand-50 text-brand-600">
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
            className="sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] mt-4 flex items-center gap-2 rounded-full bg-white p-1.5 shadow-md ring-1 ring-gray-200"
          >
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="प्रश्न सोध्नुहोस्…"
              className="flex-1 rounded-full border-0 bg-transparent px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-0 font-deva"
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
      </div>
    </div>
  );
}
