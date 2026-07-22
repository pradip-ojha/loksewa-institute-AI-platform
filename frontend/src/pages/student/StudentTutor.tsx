import { useEffect, useRef, useState } from "react";
import {
  Sparkles, GraduationCap, BookOpen, Plus, Trash2,
  PanelLeftClose, PanelLeftOpen, MessageSquare,
} from "lucide-react";
import { tutorService, type TutorSessionItem } from "../../services/tutor";
import { useStudentExam } from "../../context/StudentExamContext";
import { PageHeader, ConfirmDialog } from "../../components/ui";
import { ChatTurnView, ChatInput, ChatEmptyState, useAutoScrollEnd } from "../../components/chat";

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

export function StudentTutor() {
  const { exams, selectedExamId: examId, isLoading: examsLoading } = useStudentExam();
  const [sessions, setSessions] = useState<TutorSessionItem[]>([]);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  // null = a fresh "new chat" — the backend creates the session on the first ask.
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  // Two independent states so CSS breakpoints (not JS width checks) drive which list
  // shows: the static desktop column vs the mobile overlay drawer. Resize-safe.
  const [desktopListOpen, setDesktopListOpen] = useState(true);
  const [mobileListOpen, setMobileListOpen] = useState(false);
  const [isWide, setIsWide] = useState(() => window.matchMedia("(min-width: 1024px)").matches);
  const [pendingDelete, setPendingDelete] = useState<TutorSessionItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const endRef = useAutoScrollEnd(turns);
  // Tracks the in-flight stream so we can abort it (unmount / exam switch / session
  // switch), stopping backend token generation and avoiding setState-after-unmount.
  const abortRef = useRef<AbortController | null>(null);

  // Keep a display-only breakpoint flag in sync (drives the toggle target + icon);
  // sidebar VISIBILITY stays CSS-gated so a resize can never strand the drawer.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const on = () => setIsWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

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

  function toggleList() {
    if (isWide) setDesktopListOpen((v) => !v);
    else setMobileListOpen((v) => !v);
  }

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
    setMobileListOpen(false);
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
    setMobileListOpen(false);
  }

  async function confirmDelete() {
    const target = pendingDelete;
    if (!target) return;
    setDeleting(true);
    try {
      await tutorService.deleteSession(target.id);
    } catch {
      setDeleting(false);
      return; // deletion failed — leave the list untouched
    }
    const remaining = sessions.filter((s) => s.id !== target.id);
    setSessions(remaining);
    if (sessionId === target.id) {
      if (remaining.length > 0) void openSession(remaining[0].id);
      else newChat();
    }
    setDeleting(false);
    setPendingDelete(null);
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

  const sessionListBody = (
    <>
      <div className="mb-2 flex items-center justify-between px-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">कुराकानीहरू</p>
        <button
          onClick={newChat}
          className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold text-brand-600 transition-colors hover:bg-brand-50"
        >
          <Plus className="h-3.5 w-3.5" /> नयाँ
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-y-auto scrollbar-thin">
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
                <MessageSquare className={`h-3.5 w-3.5 flex-shrink-0 ${s.id === sessionId ? "text-brand-600" : "text-gray-300"}`} />
                <span className="truncate text-sm text-gray-800 font-deva">{s.title}</span>
              </span>
              <span className="mt-0.5 block pl-5 text-[11px] text-gray-400">
                {new Date(s.last_message_at).toLocaleDateString()} · {s.message_count} प्रश्न
              </span>
            </button>
            <button
              onClick={() => setPendingDelete(s)}
              className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-md text-gray-300 opacity-0 transition-all hover:bg-danger-50 hover:text-danger-600 focus:opacity-100 group-hover:opacity-100"
              aria-label="Delete chat"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </>
  );

  return (
    <div className="flex min-h-[calc(100vh-7rem)] flex-col pb-20 lg:h-[calc(100vh-3rem)] lg:min-h-0 lg:overflow-hidden lg:pb-0">
      <PageHeader
        title="AI Tutor"
        description="नोट र पुस्तकमा आधारित AI शिक्षकसँग जे पनि सोध्नुहोस्"
        icon={<Sparkles className="h-5 w-5" />}
        actions={
          <div className="flex items-center gap-2">
            <button
              onClick={toggleList}
              className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-gray-600 ring-1 ring-gray-200 transition-colors hover:bg-gray-50"
              aria-label="Toggle chat list"
              title="कुराकानी सूची"
            >
              {(isWide ? desktopListOpen : mobileListOpen) ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
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

      <div className="relative flex flex-1 items-stretch gap-4 lg:min-h-0">
        {/* Desktop session sidebar (static column) */}
        {desktopListOpen && (
          <aside className="hidden lg:flex lg:w-64 lg:flex-shrink-0 lg:flex-col lg:rounded-lg lg:border lg:border-gray-200 lg:bg-white lg:p-3">
            {sessionListBody}
          </aside>
        )}

        {/* Mobile session sidebar (overlay drawer) — CSS-gated to lg:hidden */}
        {mobileListOpen && (
          <div className="lg:hidden">
            <div className="fixed inset-0 z-30 bg-black/30" onClick={() => setMobileListOpen(false)} aria-hidden />
            <aside className="fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-white p-3 shadow-pop">
              {sessionListBody}
            </aside>
          </div>
        )}

        {/* ── Active conversation ────────────────────────────────────────────────── */}
        <div className="flex min-w-0 flex-1 flex-col lg:min-h-0">
          <div className="flex-1 space-y-4 lg:min-h-0 lg:overflow-y-auto lg:pr-1 scrollbar-thin">
            {historyLoaded && turns.length === 0 && (
              <ChatEmptyState
                icon={<GraduationCap className="h-5 w-5" />}
                title="AI Tutor लाई सोध्नुहोस्"
                description="उपलब्ध अध्यायबारे जे पनि सोध्नुहोस् — उत्तर नोट र पुस्तकमा आधारित हुन्छ।"
                starters={STARTER_QUESTIONS}
                onAsk={ask}
              />
            )}

            {turns.map((turn, i) => (
              <ChatTurnView
                key={i}
                question={turn.question}
                answer={turn.answer}
                loading={turn.loading}
                error={turn.error}
                followUps={turn.followUps}
                onFollowUp={ask}
                beforeAnswer={
                  turn.topic ? (
                    <div className="mb-2 inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-medium text-brand-700 ring-1 ring-brand-100">
                      <BookOpen className="h-3 w-3" />
                      <span className="font-deva">{turn.topic}</span>
                    </div>
                  ) : undefined
                }
              />
            ))}
            <div ref={endRef} />
          </div>

          <ChatInput
            value={question}
            onChange={setQuestion}
            onSubmit={() => void ask(question)}
            disabled={busy}
            className="lg:static"
          />
        </div>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="कुराकानी मेटाउने?"
        description="यसका सबै प्रश्नोत्तर हट्नेछन्।"
        confirmLabel="मेटाउनुहोस्"
        cancelLabel="रद्द गर्नुहोस्"
        tone="danger"
        loading={deleting}
        onConfirm={() => void confirmDelete()}
        onClose={() => setPendingDelete(null)}
      />
    </div>
  );
}
