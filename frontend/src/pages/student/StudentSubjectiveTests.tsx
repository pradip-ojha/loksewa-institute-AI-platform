import { useCallback, useEffect, useRef, useState } from "react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { subjectiveTestsService } from "../../services/subjectiveTests";
import type { AnswerResult, StudentTestListItem } from "../../services/subjectiveTests";
import { getErrorMessage } from "../../utils/error";

type View = "list" | "detail" | "result";

const STATUS_LABEL: Record<string, string> = {
  none: "Not submitted",
  processing: "Checking…",
  needs_reupload: "Re-upload needed",
  checked: "Checked",
  failed: "Failed",
};
const STATUS_COLOR: Record<string, string> = {
  none: "text-gray-500",
  processing: "text-blue-600",
  needs_reupload: "text-yellow-600",
  checked: "text-green-600",
  failed: "text-red-600",
};

export function StudentSubjectiveTests() {
  const [view, setView] = useState<View>("list");
  const [tests, setTests] = useState<StudentTestListItem[]>([]);
  const [activeTestId, setActiveTestId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadTests = useCallback(async () => {
    setLoading(true);
    try {
      setTests(await subjectiveTestsService.listStudentTests());
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load tests."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (view === "list") void loadTests(); }, [view, loadTests]);

  function openTest(id: string) {
    setActiveTestId(id);
    setView("detail");
  }
  function viewResult(id: string) {
    setActiveTestId(id);
    setView("result");
  }

  if (view === "detail" && activeTestId) {
    return <UploadView testId={activeTestId} onBack={() => setView("list")} onChecked={() => viewResult(activeTestId)} />;
  }
  if (view === "result" && activeTestId) {
    return <ResultView testId={activeTestId} onBack={() => setView("list")} />;
  }

  return (
    <div className="pb-20">
      <h1 className="mb-4 text-xl font-bold text-gray-900">Subjective Tests</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : tests.length === 0 ? (
        <p className="text-sm text-gray-500">No subjective tests available right now.</p>
      ) : (
        <div className="space-y-3">
          {tests.map((t) => (
            <div key={t.test_id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="font-semibold text-gray-800">{t.display_name}</h3>
                  <p className="text-xs text-gray-400">
                    {t.num_questions} questions · {t.total_marks} marks · {t.total_time_minutes} min
                  </p>
                </div>
                <span className={`text-xs font-medium ${STATUS_COLOR[t.submission_status] ?? "text-gray-500"}`}>
                  {STATUS_LABEL[t.submission_status] ?? t.submission_status}
                </span>
              </div>
              <div className="mt-3 flex gap-2">
                {t.submission_status === "checked" ? (
                  <button onClick={() => viewResult(t.test_id)} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white">
                    View Result {t.total_marks_awarded != null ? `(${t.total_marks_awarded}/${t.total_marks})` : ""}
                  </button>
                ) : t.submission_status === "processing" ? (
                  <button onClick={() => viewResult(t.test_id)} className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm font-medium text-gray-700">
                    Track Progress
                  </button>
                ) : (
                  <button onClick={() => openTest(t.test_id)} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white">
                    {t.submission_status === "needs_reupload" ? "Re-upload Answer" : "Upload Answer"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Upload ──────────────────────────────────────────────────────────────────────

function UploadView({ testId, onBack, onChecked }: { testId: string; onBack: () => void; onChecked: () => void }) {
  const [detail, setDetail] = useState<{ display_name: string; question_paper_url: string | null } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    subjectiveTestsService.getStudentTest(testId)
      .then((d) => setDetail({ display_name: d.display_name, question_paper_url: d.question_paper_url }))
      .catch((err) => setError(getErrorMessage(err, "Failed to load test.")));
  }, [testId]);

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const file = fileRef.current?.files?.[0];
    if (!file) return setError("Select your answer sheet (PDF or image).");
    const fd = new FormData();
    fd.append("answer_sheet", file);
    setSubmitting(true);
    try {
      const job = await subjectiveTestsService.uploadAnswer(testId, fd);
      setJobId(job.id);
    } catch (err) {
      setError(getErrorMessage(err, "Upload failed."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleJobComplete(job: JobState) {
    const out = job.output_reference as { needs_reupload?: boolean } | null | undefined;
    if (out?.needs_reupload) {
      // Stay here so the student can re-upload a clearer image.
      return;
    }
    onChecked();
  }

  return (
    <div className="pb-20">
      <button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button>
      <h1 className="mb-1 text-xl font-bold text-gray-900">{detail?.display_name ?? "Subjective Test"}</h1>
      {detail?.question_paper_url && (
        <a href={detail.question_paper_url} target="_blank" rel="noreferrer" className="mb-4 inline-block text-sm text-brand-700 hover:underline">
          View / download question paper
        </a>
      )}

      <form onSubmit={handleUpload} className="mt-3 space-y-3 rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
        <label className="block text-sm font-medium text-gray-700">Upload your answer sheet (PDF or photo)</label>
        <input ref={fileRef} type="file" accept=".pdf,image/jpeg,image/png,image/webp" className="text-sm" />
        <p className="text-xs text-gray-400">Make sure the photo is clear, well-lit, and not tilted. You can re-upload once if quality is poor.</p>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" disabled={submitting || !!jobId} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
          {submitting ? "Uploading…" : "Submit for Checking"}
        </button>
      </form>

      {jobId && (
        <>
          <JobStatusPoller jobId={jobId} className="mt-4" onComplete={handleJobComplete} onFail={handleJobComplete} />
          <ReuploadHint jobId={jobId} onReupload={() => { setJobId(null); }} />
        </>
      )}
    </div>
  );
}

function ReuploadHint({ jobId, onReupload }: { jobId: string; onReupload: () => void }) {
  // Lightweight: when the poller-completed job flagged needs_reupload we surface
  // a reset button. We re-check the job once here for the flag.
  const [needs, setNeeds] = useState(false);
  useEffect(() => {
    let active = true;
    const id = setInterval(async () => {
      try {
        const { default: api } = await import("../../services/api");
        const { data } = await api.get<JobState>(`/api/jobs/${jobId}`);
        if (!active) return;
        const out = data.output_reference as { needs_reupload?: boolean } | null | undefined;
        if (data.status === "completed" && out?.needs_reupload) {
          setNeeds(true);
          clearInterval(id);
        }
        if (data.status === "completed" || data.status === "failed") clearInterval(id);
      } catch {
        /* ignore */
      }
    }, 2500);
    return () => { active = false; clearInterval(id); };
  }, [jobId]);

  if (!needs) return null;
  return (
    <div className="mt-3 rounded-lg bg-yellow-50 p-3 text-sm text-yellow-800">
      The image quality was too low to check reliably. Please re-upload a clearer photo.
      <button onClick={onReupload} className="ml-2 font-medium underline">Re-upload</button>
    </div>
  );
}

// ── Result ──────────────────────────────────────────────────────────────────────

function ResultView({ testId, onBack }: { testId: string; onBack: () => void }) {
  const [result, setResult] = useState<AnswerResult | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setResult(await subjectiveTestsService.getResult(testId));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load result."));
    }
  }, [testId]);

  useEffect(() => {
    void load();
    // Poll while still processing.
    const id = setInterval(async () => {
      try {
        const r = await subjectiveTestsService.getResult(testId);
        setResult(r);
        if (r.status !== "processing") clearInterval(id);
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(id);
  }, [testId, load]);

  if (error) return <div className="pb-20"><button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button><p className="text-sm text-red-600">{error}</p></div>;
  if (!result) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div className="pb-20">
      <button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button>
      <h1 className="mb-1 text-xl font-bold text-gray-900">{result.display_name}</h1>

      {result.status === "processing" && (
        <p className="text-sm text-blue-600">Your answer sheet is being checked. This page updates automatically…</p>
      )}

      {result.status === "needs_reupload" && (
        <div className="rounded-lg bg-yellow-50 p-4 text-sm text-yellow-800">
          <p className="font-medium">Re-upload needed</p>
          <p className="mt-1">{result.quality?.quality_notes ?? "Image quality was too low."}</p>
          {result.can_reupload && (
            <button onClick={onBack} className="mt-2 font-medium underline">Go back and re-upload</button>
          )}
        </div>
      )}

      {result.status === "failed" && (
        <p className="text-sm text-red-600">Checking failed. Please try uploading again.</p>
      )}

      {result.status === "checked" && (
        <>
          <div className="my-4 rounded-xl bg-white p-5 text-center shadow-sm ring-1 ring-gray-100">
            <p className="text-sm text-gray-500">Total Marks</p>
            <p className="text-3xl font-bold text-brand-700">
              {result.total_marks_awarded} <span className="text-lg text-gray-400">/ {result.total_marks_possible}</span>
            </p>
            {result.checked_pdf_url && (
              <a href={result.checked_pdf_url} target="_blank" rel="noreferrer" className="mt-3 inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white">
                View Checked PDF
              </a>
            )}
          </div>

          <div className="space-y-3">
            {result.questions.map((q) => (
              <div key={q.question_number} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-gray-800">{q.question_number}</span>
                  <span className="text-sm font-semibold text-brand-700">{q.marks_awarded} / {q.marks_possible}</span>
                </div>
                {q.question_text && <p className="mt-1 text-xs text-gray-500">{q.question_text}</p>}
                {q.feedback && <p className="mt-2 text-sm text-gray-700">{q.feedback}</p>}
                {q.mistakes.length > 0 && (
                  <ul className="mt-2 list-disc pl-5 text-xs text-red-600">
                    {q.mistakes.map((m, i) => <li key={i}>{m}</li>)}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
