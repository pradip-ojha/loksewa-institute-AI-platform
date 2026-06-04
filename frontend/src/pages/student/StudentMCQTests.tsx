import { useCallback, useEffect, useRef, useState } from "react";
import { mcqTestsService } from "../../services/mcqTests";
import type {
  AttemptStart, AttemptResult, StudentTestSet, AttemptHistoryItem, StudentAnalytics,
} from "../../services/mcqTests";
import { getErrorMessage } from "../../utils/error";

type View = "list" | "taking" | "result";
type Tab = "tests" | "history" | "analytics";

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60).toString().padStart(2, "0");
  const s = Math.floor(secs % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function pct(correct: number, total: number): number {
  return total > 0 ? Math.round((correct / total) * 100) : 0;
}

// ── Tests tab ─────────────────────────────────────────────────────────────────

function TestsTab({ tests, onStart, onViewResult, startingId, busy, error }: {
  tests: StudentTestSet[];
  onStart: (setId: string) => void;
  onViewResult: (attemptId: string) => void;
  startingId: string | null;
  busy: boolean;
  error: string;
}) {
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (tests.length === 0) return <p className="text-sm text-gray-500">No tests available right now. Check back later.</p>;

  return (
    <div className="space-y-3">
      {tests.map((t) => (
        <div key={t.set_id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
          <p className="font-medium text-gray-900">{t.test_name}</p>
          <p className="text-xs text-gray-500">{t.set_name}</p>
          <p className="mt-1 text-xs text-gray-500">{t.num_questions} questions · {t.total_time_minutes} min</p>

          {t.attempt_status === "submitted" ? (
            <div className="mt-3 flex items-center justify-between">
              <span className="rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-700">
                Score: {t.score}/{t.num_questions}
              </span>
              <button onClick={() => t.attempt_id && onViewResult(t.attempt_id)}
                className="rounded-lg border border-brand-600 px-4 py-1.5 text-sm font-medium text-brand-700 hover:bg-brand-50">
                View Result
              </button>
            </div>
          ) : t.attempt_status === "in_progress" ? (
            <button onClick={() => onStart(t.set_id)} disabled={busy}
              className="mt-3 w-full rounded-lg bg-yellow-500 py-2 text-sm font-medium text-white hover:bg-yellow-600 disabled:opacity-50">
              {startingId === t.set_id ? "Resuming…" : "Continue"}
            </button>
          ) : (
            <button onClick={() => onStart(t.set_id)} disabled={busy}
              className="mt-3 w-full rounded-lg bg-brand-600 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
              {startingId === t.set_id ? "Starting…" : "Start Test"}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

// ── History tab ───────────────────────────────────────────────────────────────

function HistoryTab({ items, onViewResult, error }: {
  items: AttemptHistoryItem[];
  onViewResult: (attemptId: string) => void;
  error: string;
}) {
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (items.length === 0) return <p className="text-sm text-gray-500">You haven't completed any tests yet.</p>;

  return (
    <div className="space-y-3">
      {items.map((h) => (
        <button key={h.attempt_id} onClick={() => onViewResult(h.attempt_id)}
          className="block w-full rounded-xl bg-white p-4 text-left shadow-sm ring-1 ring-gray-100 hover:shadow-md">
          <div className="flex items-center justify-between">
            <p className="font-medium text-gray-900">{h.test_name}</p>
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
              pct(h.correct_count, h.total_questions) >= 50 ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
            }`}>
              {h.score}/{h.total_questions} · {pct(h.correct_count, h.total_questions)}%
            </span>
          </div>
          <p className="mt-1 text-xs text-gray-500">{h.set_name}</p>
          {h.submitted_at && (
            <p className="mt-1 text-xs text-gray-400">{new Date(h.submitted_at).toLocaleString()}</p>
          )}
        </button>
      ))}
    </div>
  );
}

// ── Analytics tab ─────────────────────────────────────────────────────────────

function AnalyticsTab({ data, error }: { data: StudentAnalytics | null; error: string }) {
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!data) return <p className="text-sm text-gray-500">Loading…</p>;
  if (data.total_attempts === 0)
    return <p className="text-sm text-gray-500">Complete a test to see your analytics.</p>;

  const cards = [
    { label: "Tests Taken", value: String(data.total_attempts) },
    { label: "Avg Score", value: `${data.average_score_percent}%` },
    { label: "Best Score", value: data.best_score_percent != null ? `${data.best_score_percent}%` : "—" },
    { label: "Overall Accuracy", value: `${data.overall_accuracy}%` },
  ];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3">
        {cards.map((c) => (
          <div key={c.label} className="rounded-xl bg-white p-4 text-center shadow-sm ring-1 ring-gray-100">
            <p className="text-2xl font-bold text-gray-900">{c.value}</p>
            <p className="mt-0.5 text-xs text-gray-500">{c.label}</p>
          </div>
        ))}
      </div>

      {data.weak_topics.length > 0 && (
        <div className="rounded-xl bg-red-50 p-4 ring-1 ring-red-100">
          <p className="text-sm font-semibold text-red-700">Topics to focus on</p>
          <div className="mt-2 space-y-1">
            {data.weak_topics.map((t) => (
              <div key={t.topic} className="flex items-center justify-between text-sm text-red-800">
                <span>{t.topic}</span>
                <span className="font-medium">{t.accuracy}% ({t.correct}/{t.total})</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-2 text-sm font-semibold text-gray-700">Topic-wise performance</p>
        <div className="space-y-2">
          {data.topic_performance.map((t) => (
            <div key={t.topic} className="rounded-xl bg-white p-3 shadow-sm ring-1 ring-gray-100">
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="text-gray-700">{t.topic}</span>
                <span className="text-gray-500">{t.accuracy}% ({t.correct}/{t.total})</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                <div className={`h-full rounded-full ${t.accuracy >= 60 ? "bg-green-500" : t.accuracy >= 40 ? "bg-yellow-400" : "bg-red-500"}`}
                  style={{ width: `${t.accuracy}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Result view ───────────────────────────────────────────────────────────────

function ResultView({ result, onDone }: { result: AttemptResult; onDone: () => void }) {
  return (
    <div>
      <div className="mb-5 rounded-xl bg-white p-5 text-center shadow-sm ring-1 ring-gray-100">
        <p className="text-sm text-gray-500">{result.test_name}</p>
        <p className="mt-1 text-3xl font-bold text-gray-900">{result.score}/{result.total_questions}</p>
        <p className="mt-1 text-sm font-medium text-brand-600">{pct(result.correct_count, result.total_questions)}% correct</p>
        {result.time_taken_seconds != null && (
          <p className="mt-1 text-xs text-gray-400">Time taken: {formatTime(result.time_taken_seconds)}</p>
        )}
      </div>

      <div className="space-y-3">
        {result.questions.map((q, i) => (
          <div key={q.id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-medium text-gray-900">{i + 1}. {q.question_text}</p>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${q.is_correct ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                {q.is_correct ? "Correct" : "Wrong"}
              </span>
            </div>
            <ul className="mt-2 space-y-1">
              {q.options.map((o) => {
                const isCorrect = q.correct_option_ids.includes(o.id);
                const isSelected = q.selected_option_id === o.id;
                return (
                  <li key={o.id}
                    className={`rounded px-2 py-1 text-sm ${
                      isCorrect ? "bg-green-50 font-medium text-green-700"
                        : isSelected ? "bg-red-50 text-red-700" : "text-gray-600"
                    }`}>
                    {o.label}. {o.text}
                    {isCorrect && " ✓"}
                    {isSelected && !isCorrect && " (your answer)"}
                  </li>
                );
              })}
            </ul>
            {q.explanation && <p className="mt-2 rounded bg-gray-50 p-2 text-xs text-gray-600">{q.explanation}</p>}
            <p className="mt-1 text-xs text-gray-400">{q.topic ?? "—"} · {q.complexity}</p>
          </div>
        ))}
      </div>

      <button onClick={onDone} className="mt-5 w-full rounded-lg bg-gray-900 py-2 text-sm font-medium text-white hover:bg-gray-800">
        Back
      </button>
    </div>
  );
}

// ── Taking view ───────────────────────────────────────────────────────────────

function TakingView({ attempt, onSubmitted }: { attempt: AttemptStart; onSubmitted: (r: AttemptResult) => void }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [current, setCurrent] = useState(0);
  const [remaining, setRemaining] = useState(attempt.total_time_minutes * 60);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const startRef = useRef(Date.now());
  const submittedRef = useRef(false);

  const questions = attempt.questions;

  const doSubmit = useCallback(async () => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    setSubmitting(true);
    setError("");
    try {
      const timeTaken = Math.round((Date.now() - startRef.current) / 1000);
      const payload = questions.map((q) => ({
        question_id: q.id,
        selected_option_id: answers[q.id] ?? null,
      }));
      const result = await mcqTestsService.submitTest(attempt.set_id, payload, timeTaken);
      onSubmitted(result);
    } catch (err) {
      submittedRef.current = false;
      setError(getErrorMessage(err, "Could not submit. Try again."));
      setSubmitting(false);
    }
  }, [answers, attempt.set_id, questions, onSubmitted]);

  // Countdown — auto-submit when it hits zero.
  useEffect(() => {
    const id = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          clearInterval(id);
          void doSubmit();
          return 0;
        }
        return r - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [doSubmit]);

  const q = questions[current];
  const answeredCount = Object.keys(answers).length;
  const lowTime = remaining <= 60;

  return (
    <div className="pb-4">
      <div className="sticky top-14 z-10 -mx-4 mb-4 flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2">
        <span className="text-sm font-medium text-gray-700">{attempt.test_name}</span>
        <span className={`rounded-lg px-3 py-1 text-sm font-semibold ${lowTime ? "bg-red-100 text-red-700" : "bg-brand-50 text-brand-700"}`}>
          {formatTime(remaining)}
        </span>
      </div>

      <div className="mb-4 flex flex-wrap gap-1.5">
        {questions.map((qq, i) => {
          const done = answers[qq.id] != null;
          return (
            <button key={qq.id} onClick={() => setCurrent(i)}
              className={`h-8 w-8 rounded-md text-xs font-medium ${
                i === current ? "bg-brand-600 text-white"
                  : done ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"
              }`}>
              {i + 1}
            </button>
          );
        })}
      </div>

      <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
        <p className="text-xs text-gray-400">Question {current + 1} of {questions.length}</p>
        <p className="mt-1 text-sm font-medium text-gray-900">{q.question_text}</p>
        <div className="mt-3 space-y-2">
          {q.options.map((o) => {
            const selected = answers[q.id] === o.id;
            return (
              <button key={o.id} onClick={() => setAnswers((p) => ({ ...p, [q.id]: o.id }))}
                className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm ${
                  selected ? "border-brand-500 bg-brand-50 text-brand-800" : "border-gray-200 text-gray-700 hover:bg-gray-50"
                }`}>
                <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-xs ${
                  selected ? "border-brand-500 bg-brand-500 text-white" : "border-gray-300"
                }`}>{o.label}</span>
                {o.text}
              </button>
            );
          })}
        </div>
      </div>

      <div className="mt-4 flex gap-2">
        <button disabled={current === 0} onClick={() => setCurrent((c) => c - 1)}
          className="flex-1 rounded-lg border border-gray-300 py-2 text-sm font-medium text-gray-700 disabled:opacity-40">
          Previous
        </button>
        {current < questions.length - 1 ? (
          <button onClick={() => setCurrent((c) => c + 1)}
            className="flex-1 rounded-lg bg-gray-900 py-2 text-sm font-medium text-white hover:bg-gray-800">
            Next
          </button>
        ) : (
          <button onClick={doSubmit} disabled={submitting}
            className="flex-1 rounded-lg bg-brand-600 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
            {submitting ? "Submitting…" : "Submit Test"}
          </button>
        )}
      </div>

      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

      <button onClick={doSubmit} disabled={submitting}
        className="mt-4 w-full text-center text-xs text-gray-400 hover:text-gray-600">
        Submit now ({answeredCount}/{questions.length} answered)
      </button>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function StudentMCQTests() {
  const [view, setView] = useState<View>("list");
  const [tab, setTab] = useState<Tab>("tests");

  const [tests, setTests] = useState<StudentTestSet[]>([]);
  const [history, setHistory] = useState<AttemptHistoryItem[]>([]);
  const [analytics, setAnalytics] = useState<StudentAnalytics | null>(null);

  const [attempt, setAttempt] = useState<AttemptStart | null>(null);
  const [result, setResult] = useState<AttemptResult | null>(null);

  const [startingId, setStartingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [tabError, setTabError] = useState("");

  const loadTests = useCallback(async () => {
    try {
      setTests(await mcqTestsService.listStudentTests());
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  const loadHistory = useCallback(async () => {
    setTabError("");
    try {
      setHistory(await mcqTestsService.getHistory());
    } catch (err) {
      setTabError(getErrorMessage(err));
    }
  }, []);

  const loadAnalytics = useCallback(async () => {
    setTabError("");
    try {
      setAnalytics(await mcqTestsService.getAnalytics());
    } catch (err) {
      setTabError(getErrorMessage(err));
    }
  }, []);

  useEffect(() => { loadTests(); }, [loadTests]);

  // Lazy-load each tab's data when it's opened.
  useEffect(() => {
    if (view !== "list") return;
    setTabError("");
    if (tab === "tests") loadTests();
    else if (tab === "history") loadHistory();
    else if (tab === "analytics") loadAnalytics();
  }, [tab, view, loadTests, loadHistory, loadAnalytics]);

  // Start OR continue — both resume the single attempt (idempotent on the backend).
  async function handleStart(setId: string) {
    if (startingId) return; // guard against duplicate start requests
    setStartingId(setId);
    setError("");
    try {
      const a = await mcqTestsService.startTest(setId);
      setAttempt(a);
      setView("taking");
    } catch (err) {
      setError(getErrorMessage(err, "Could not start the test."));
      // A 409 (already submitted) can happen if the list was stale — refresh it.
      loadTests();
    } finally {
      setStartingId(null);
    }
  }

  async function handleViewResult(attemptId: string) {
    setError("");
    try {
      const r = await mcqTestsService.getResult(attemptId);
      setResult(r);
      setView("result");
    } catch (err) {
      setError(getErrorMessage(err, "Could not load the result."));
    }
  }

  function handleSubmitted(r: AttemptResult) {
    setResult(r);
    setView("result");
  }

  function handleDone() {
    setAttempt(null);
    setResult(null);
    setView("list");
    // Refresh everything so statuses/history/analytics reflect the new submission.
    loadTests();
    setHistory([]);
    setAnalytics(null);
  }

  if (view === "taking" && attempt) return <TakingView attempt={attempt} onSubmitted={handleSubmitted} />;
  if (view === "result" && result) return <ResultView result={result} onDone={handleDone} />;

  const TABS: { key: Tab; label: string }[] = [
    { key: "tests", label: "Tests" },
    { key: "history", label: "Results" },
    { key: "analytics", label: "Analytics" },
  ];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold text-gray-900">MCQ Tests</h1>

      <div className="mb-4 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key ? "border-b-2 border-brand-600 text-brand-700" : "text-gray-500 hover:text-gray-700"
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === "tests" && (
        <TestsTab tests={tests} onStart={handleStart} onViewResult={handleViewResult}
          startingId={startingId} busy={startingId !== null} error={error} />
      )}
      {tab === "history" && <HistoryTab items={history} onViewResult={handleViewResult} error={tabError} />}
      {tab === "analytics" && <AnalyticsTab data={analytics} error={tabError} />}
    </div>
  );
}
