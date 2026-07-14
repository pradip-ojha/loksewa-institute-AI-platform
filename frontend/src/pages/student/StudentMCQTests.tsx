import { useCallback, useEffect, useRef, useState } from "react";
import {
  HelpCircle, CheckCircle2, XCircle, Clock, ChevronLeft, ChevronRight, Trophy,
  TrendingUp, AlertTriangle, ArrowLeft, Play,
} from "lucide-react";
import { mcqTestsService } from "../../services/mcqTests";
import type {
  AttemptStart, AttemptResult, StudentTestSet, AttemptHistoryItem, StudentAnalytics,
} from "../../services/mcqTests";
import { useStudentExam } from "../../context/StudentExamContext";
import { getErrorMessage } from "../../utils/error";
import { PageHeader, Card, Button, Badge, Tabs, EmptyState, Alert, cn } from "../../components/ui";
import { RichText } from "../../components/content/RichText";

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
  if (error) return <Alert>{error}</Alert>;
  if (tests.length === 0)
    return <EmptyState icon={<HelpCircle className="h-6 w-6" />} title="अहिले कुनै test छैन" description="Check back later." />;

  return (
    <div className="space-y-3">
      {tests.map((t) => (
        <Card key={t.set_id}>
          <p className="font-semibold text-gray-900 font-deva">{t.test_name}</p>
          <p className="text-xs text-gray-500">{t.set_name}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <Badge tone="neutral">{t.num_questions} questions</Badge>
            <Badge tone="neutral">{t.total_time_minutes} min</Badge>
          </div>

          {t.attempt_status === "submitted" ? (
            <div className="mt-3 flex items-center justify-between">
              <Badge tone="success" icon={<Trophy className="h-3.5 w-3.5" />}>
                Score: {t.score}/{t.num_questions}
              </Badge>
              <Button size="sm" variant="secondary" onClick={() => t.attempt_id && onViewResult(t.attempt_id)}>
                View Result
              </Button>
            </div>
          ) : t.attempt_status === "in_progress" ? (
            <Button fullWidth className="mt-3" variant="primary" loading={startingId === t.set_id} disabled={busy}
              icon={<Play className="h-4 w-4" />} onClick={() => onStart(t.set_id)}>
              Continue
            </Button>
          ) : (
            <Button fullWidth className="mt-3" loading={startingId === t.set_id} disabled={busy}
              icon={<Play className="h-4 w-4" />} onClick={() => onStart(t.set_id)}>
              Start Test
            </Button>
          )}
        </Card>
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
  if (error) return <Alert>{error}</Alert>;
  if (items.length === 0)
    return <EmptyState icon={<Trophy className="h-6 w-6" />} title="कुनै test पूरा गरिएको छैन" />;

  return (
    <div className="space-y-3">
      {items.map((h) => {
        const p = pct(h.correct_count, h.total_questions);
        return (
          <Card key={h.attempt_id} interactive onClick={() => onViewResult(h.attempt_id)}>
            <div className="flex items-center justify-between gap-2">
              <p className="font-semibold text-gray-900 font-deva">{h.test_name}</p>
              <Badge tone={p >= 50 ? "success" : "danger"}>{h.score}/{h.total_questions} · {p}%</Badge>
            </div>
            <p className="mt-1 text-xs text-gray-500">{h.set_name}</p>
            {h.submitted_at && (
              <p className="mt-1 text-xs text-gray-400">{new Date(h.submitted_at).toLocaleString()}</p>
            )}
          </Card>
        );
      })}
    </div>
  );
}

// ── Analytics tab ─────────────────────────────────────────────────────────────

function AnalyticsTab({ data, error }: { data: StudentAnalytics | null; error: string }) {
  if (error) return <Alert>{error}</Alert>;
  if (!data) return <p className="text-sm text-gray-500">Loading…</p>;
  if (data.total_attempts === 0)
    return <EmptyState icon={<TrendingUp className="h-6 w-6" />} title="Complete a test to see analytics" />;

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
          <Card key={c.label} className="text-center">
            <p className="text-2xl font-bold text-gray-900">{c.value}</p>
            <p className="mt-0.5 text-xs text-gray-500">{c.label}</p>
          </Card>
        ))}
      </div>

      {data.weak_topics.length > 0 && (
        <div className="rounded-lg border border-danger-100 bg-danger-50 p-4">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-danger-700">
            <AlertTriangle className="h-4 w-4" /> Topics to focus on
          </p>
          <div className="mt-2 space-y-1">
            {data.weak_topics.map((t) => (
              <div key={t.topic} className="flex items-center justify-between text-sm text-danger-800 font-deva">
                <span>{t.topic}</span>
                <span className="font-medium tabular-nums">{t.accuracy}% ({t.correct}/{t.total})</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div>
        <p className="mb-2 text-sm font-semibold text-gray-700">Topic-wise performance</p>
        <div className="space-y-2">
          {data.topic_performance.map((t) => (
            <Card key={t.topic} className="p-3">
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="text-gray-700 font-deva">{t.topic}</span>
                <span className="text-gray-500 tabular-nums">{t.accuracy}% ({t.correct}/{t.total})</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                <div className={cn("h-full rounded-full transition-all", t.accuracy >= 60 ? "bg-success-500" : t.accuracy >= 40 ? "bg-warning-400" : "bg-danger-500")}
                  style={{ width: `${t.accuracy}%` }} />
              </div>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Result view ───────────────────────────────────────────────────────────────

function ResultView({ result, onDone }: { result: AttemptResult; onDone: () => void }) {
  const p = pct(result.correct_count, result.total_questions);
  const tone = p >= 60 ? "bg-success-600" : p >= 40 ? "bg-warning-600" : "bg-danger-600";
  return (
    <div className="pb-20">
      <button onClick={onDone} className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:text-brand-800">
        <ArrowLeft className="h-4 w-4" /> Back
      </button>
      <div className={`mb-5 overflow-hidden rounded-lg ${tone} p-6 text-center text-white`}>
        <p className="text-sm font-medium text-white/80 font-deva">{result.test_name}</p>
        <p className="mt-1 text-4xl font-bold tracking-tight">{result.score}/{result.total_questions}</p>
        <p className="mt-1 text-sm font-medium text-white/90">{p}% correct</p>
        {result.time_taken_seconds != null && (
          <p className="mt-1 text-xs text-white/70">Time taken: {formatTime(result.time_taken_seconds)}</p>
        )}
      </div>

      <div className="space-y-3">
        {result.questions.map((q, i) => (
          <Card key={q.id}>
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm font-semibold text-gray-900 font-deva">{i + 1}. {q.question_text}</p>
              <Badge tone={q.is_correct ? "success" : "danger"} className="shrink-0"
                icon={q.is_correct ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}>
                {q.is_correct ? "Correct" : "Wrong"}
              </Badge>
            </div>
            <ul className="mt-2 space-y-1.5">
              {q.options.map((o) => {
                const isCorrect = q.correct_option_ids.includes(o.id);
                const isSelected = q.selected_option_id === o.id;
                return (
                  <li key={o.id}
                    className={cn(
                      "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-deva",
                      isCorrect ? "bg-success-50 font-medium text-success-700 ring-1 ring-success-100"
                        : isSelected ? "bg-danger-50 text-danger-700 ring-1 ring-danger-100" : "text-gray-600",
                    )}>
                    <span className="font-semibold">{o.label}.</span>
                    <span className="flex-1">{o.text}</span>
                    {isCorrect && <CheckCircle2 className="h-4 w-4 text-success-500" />}
                    {isSelected && !isCorrect && <span className="text-xs text-danger-500">your answer</span>}
                  </li>
                );
              })}
            </ul>
            {q.explanation && (
              <div className="mt-2 rounded-xl bg-brand-50/50 p-3 ring-1 ring-brand-100/60">
                <RichText size="sm">{q.explanation}</RichText>
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-1.5">
              {q.topic && <Badge tone="neutral">{q.topic}</Badge>}
              <Badge tone="accent" className="capitalize">{q.complexity}</Badge>
            </div>
          </Card>
        ))}
      </div>

      <Button fullWidth variant="subtle" className="mt-5" onClick={onDone}>Back to Tests</Button>
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
      <div className="sticky top-14 z-10 -mx-4 mb-4 flex items-center justify-between border-b border-gray-200 bg-white px-4 py-2.5">
        <span className="text-sm font-semibold text-gray-700 font-deva">{attempt.test_name}</span>
        <span className={cn(
          "inline-flex items-center gap-1.5 rounded-lg px-3 py-1 text-sm font-bold tabular-nums",
          lowTime ? "animate-pulse bg-danger-100 text-danger-700" : "bg-brand-50 text-brand-700",
        )}>
          <Clock className="h-4 w-4" /> {formatTime(remaining)}
        </span>
      </div>

      <div className="mb-4 flex flex-wrap gap-1.5">
        {questions.map((qq, i) => {
          const done = answers[qq.id] != null;
          return (
            <button key={qq.id} onClick={() => setCurrent(i)}
              className={cn(
                "h-8 w-8 rounded-lg text-xs font-semibold transition-colors",
                i === current ? "bg-brand-600 text-white shadow-sm"
                  : done ? "bg-success-100 text-success-700" : "bg-gray-100 text-gray-500 hover:bg-gray-200",
              )}>
              {i + 1}
            </button>
          );
        })}
      </div>

      <Card>
        <p className="text-xs font-medium text-gray-400">Question {current + 1} of {questions.length}</p>
        <p className="mt-1 text-base font-semibold text-gray-900 font-deva">{q.question_text}</p>
        <div className="mt-4 space-y-2">
          {q.options.map((o) => {
            const selected = answers[q.id] === o.id;
            return (
              <button key={o.id} onClick={() => setAnswers((p) => ({ ...p, [q.id]: o.id }))}
                className={cn(
                  "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm font-deva transition-all",
                  selected ? "border-brand-500 bg-brand-50 text-brand-800 ring-1 ring-brand-200" : "border-gray-200 text-gray-700 hover:border-gray-300 hover:bg-gray-50",
                )}>
                <span className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  selected ? "border-brand-500 bg-brand-500 text-white" : "border-gray-300 text-gray-500",
                )}>{o.label}</span>
                {o.text}
              </button>
            );
          })}
        </div>
      </Card>

      <div className="mt-4 flex gap-2">
        <Button variant="secondary" fullWidth disabled={current === 0} icon={<ChevronLeft className="h-4 w-4" />}
          onClick={() => setCurrent((c) => c - 1)}>
          Previous
        </Button>
        {current < questions.length - 1 ? (
          <Button variant="subtle" fullWidth iconRight={<ChevronRight className="h-4 w-4" />}
            onClick={() => setCurrent((c) => c + 1)}>
            Next
          </Button>
        ) : (
          <Button fullWidth loading={submitting} onClick={doSubmit}>Submit Test</Button>
        )}
      </div>

      {error && <Alert className="mt-3">{error}</Alert>}

      <button onClick={doSubmit} disabled={submitting}
        className="mt-4 w-full text-center text-xs text-gray-400 hover:text-gray-600">
        Submit now ({answeredCount}/{questions.length} answered)
      </button>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function StudentMCQTests() {
  const { selectedExamId } = useStudentExam();
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
      setTests(await mcqTestsService.listStudentTests(selectedExamId));
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, [selectedExamId]);

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

  useEffect(() => {
    if (view !== "list") return;
    setTabError("");
    if (tab === "tests") loadTests();
    else if (tab === "history") loadHistory();
    else if (tab === "analytics") loadAnalytics();
  }, [tab, view, loadTests, loadHistory, loadAnalytics]);

  async function handleStart(setId: string) {
    if (startingId) return;
    setStartingId(setId);
    setError("");
    try {
      const a = await mcqTestsService.startTest(setId);
      setAttempt(a);
      setView("taking");
    } catch (err) {
      setError(getErrorMessage(err, "Could not start the test."));
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
    loadTests();
    setHistory([]);
    setAnalytics(null);
  }

  if (view === "taking" && attempt) return <TakingView attempt={attempt} onSubmitted={handleSubmitted} />;
  if (view === "result" && result) return <ResultView result={result} onDone={handleDone} />;

  return (
    <div className="pb-20">
      <PageHeader title="MCQ Tests" description="परीक्षा दिनुहोस् र तुरुन्तै नतिजा हेर्नुहोस्" icon={<HelpCircle className="h-5 w-5" />} />

      <Tabs
        className="mb-4"
        value={tab}
        onChange={(id) => setTab(id as Tab)}
        items={[
          { id: "tests", label: "Tests" },
          { id: "history", label: "Results" },
          { id: "analytics", label: "Analytics" },
        ]}
      />

      {tab === "tests" && (
        <TestsTab tests={tests} onStart={handleStart} onViewResult={handleViewResult}
          startingId={startingId} busy={startingId !== null} error={error} />
      )}
      {tab === "history" && <HistoryTab items={history} onViewResult={handleViewResult} error={tabError} />}
      {tab === "analytics" && <AnalyticsTab data={analytics} error={tabError} />}
    </div>
  );
}
