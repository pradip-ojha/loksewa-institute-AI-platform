import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, FileCheck2, FileText, Upload, ExternalLink, AlertTriangle, Loader2, CheckCircle2,
} from "lucide-react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { subjectiveTestsService } from "../../services/subjectiveTests";
import type { AnswerResult, StudentTestListItem } from "../../services/subjectiveTests";
import { getErrorMessage } from "../../utils/error";
import { PageHeader, Card, Button, Badge, StatusBadge, EmptyState, Skeleton, Alert } from "../../components/ui";
import { RichText } from "../../components/content/RichText";
import { SectionBreakdown } from "../../components/content/LearningContent";

type View = "list" | "detail" | "result";

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
      <PageHeader
        title="Subjective Tests"
        description="उत्तरपुस्तिका अपलोड गर्नुहोस् र जाँचिएको नतिजा हेर्नुहोस्"
        icon={<FileCheck2 className="h-5 w-5" />}
      />
      {error && <Alert className="mb-3">{error}</Alert>}
      {loading ? (
        <div className="space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-28 w-full rounded-2xl" />)}</div>
      ) : tests.length === 0 ? (
        <EmptyState icon={<FileCheck2 className="h-6 w-6" />} title="अहिले कुनै subjective test छैन" />
      ) : (
        <div className="space-y-3">
          {tests.map((t) => (
            <Card key={t.test_id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="font-semibold text-gray-800 font-deva">{t.display_name}</h3>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    <Badge tone="neutral">{t.num_questions} questions</Badge>
                    <Badge tone="brand">{t.total_marks} marks</Badge>
                    <Badge tone="neutral">{t.total_time_minutes} min</Badge>
                  </div>
                </div>
                <StatusBadge status={t.submission_status} />
              </div>
              <div className="mt-3 flex gap-2">
                {t.submission_status === "checked" ? (
                  <Button size="sm" icon={<CheckCircle2 className="h-4 w-4" />} onClick={() => viewResult(t.test_id)}>
                    View Result {t.total_marks_awarded != null ? `(${t.total_marks_awarded}/${t.total_marks})` : ""}
                  </Button>
                ) : t.submission_status === "processing" ? (
                  <Button size="sm" variant="subtle" icon={<Loader2 className="h-4 w-4 animate-spin" />} onClick={() => viewResult(t.test_id)}>
                    Track Progress
                  </Button>
                ) : (
                  <Button size="sm" icon={<Upload className="h-4 w-4" />} onClick={() => openTest(t.test_id)}>
                    {t.submission_status === "needs_reupload" ? "Re-upload Answer" : "Upload Answer"}
                  </Button>
                )}
              </div>
            </Card>
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
    if (out?.needs_reupload) return;
    onChecked();
  }

  return (
    <div className="pb-20">
      <button onClick={onBack} className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:text-brand-800">
        <ArrowLeft className="h-4 w-4" /> Back
      </button>
      <h1 className="mb-1 text-xl font-bold text-gray-900 font-deva">{detail?.display_name ?? "Subjective Test"}</h1>
      {detail?.question_paper_url && (
        <a href={detail.question_paper_url} target="_blank" rel="noreferrer" className="mb-4 inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline">
          <FileText className="h-4 w-4" /> View / download question paper
        </a>
      )}

      <Card className="mt-3" padded={false}>
        <form onSubmit={handleUpload} className="space-y-3 p-5">
          <label className="block text-sm font-medium text-gray-700">Upload your answer sheet (PDF or photo)</label>
          <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-200 bg-gray-50/50 px-4 py-8 text-center transition-colors hover:border-brand-300 hover:bg-brand-50/40">
            <Upload className="h-7 w-7 text-brand-400" />
            <span className="text-sm font-medium text-gray-600">Tap to choose a file</span>
            <input ref={fileRef} type="file" accept=".pdf,image/jpeg,image/png,image/webp" className="hidden" onChange={() => setError("")} />
          </label>
          <p className="text-xs text-gray-400">Make sure the photo is clear, well-lit, and not tilted. You can re-upload once if quality is poor.</p>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" loading={submitting} disabled={!!jobId} icon={<CheckCircle2 className="h-4 w-4" />}>
            Submit for Checking
          </Button>
        </form>
      </Card>

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
    <Alert tone="warning" className="mt-3">
      The image quality was too low to check reliably. Please re-upload a clearer photo.
      <button onClick={onReupload} className="ml-2 font-semibold underline">Re-upload</button>
    </Alert>
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
    const id = setInterval(async () => {
      try {
        const r = await subjectiveTestsService.getResult(testId);
        setResult(r);
        if (r.status !== "processing") clearInterval(id);
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(id);
  }, [testId, load]);

  const backBtn = (
    <button onClick={onBack} className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:text-brand-800">
      <ArrowLeft className="h-4 w-4" /> Back
    </button>
  );

  if (error) return <div className="pb-20">{backBtn}<Alert>{error}</Alert></div>;
  if (!result) return <div className="pb-20">{backBtn}<Skeleton className="h-40 w-full rounded-2xl" /></div>;

  const pct =
    result.total_marks_possible && result.total_marks_awarded != null
      ? Math.round((result.total_marks_awarded / result.total_marks_possible) * 100)
      : null;
  const tone = pct == null ? "brand" : pct >= 60 ? "success" : pct >= 40 ? "warning" : "danger";
  const toneGrad: Record<string, string> = {
    success: "from-success-500 to-success-700",
    warning: "from-warning-500 to-warning-600",
    danger: "from-danger-500 to-danger-700",
    brand: "from-brand-500 to-brand-700",
  };

  return (
    <div className="pb-20">
      {backBtn}
      <h1 className="mb-1 text-xl font-bold text-gray-900 font-deva">{result.display_name}</h1>

      {result.status === "processing" && (
        <Alert tone="info" className="mt-2 flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" />
          Your answer sheet is being checked. This page updates automatically…
        </Alert>
      )}

      {result.status === "needs_reupload" && (
        <Alert tone="warning" className="mt-2">
          <p className="flex items-center gap-1.5 font-semibold"><AlertTriangle className="h-4 w-4" /> Re-upload needed</p>
          <p className="mt-1">{result.quality?.quality_notes ?? "Image quality was too low."}</p>
          {result.can_reupload && (
            <button onClick={onBack} className="mt-2 font-semibold underline">Go back and re-upload</button>
          )}
        </Alert>
      )}

      {result.status === "failed" && (
        <Alert className="mt-2">Checking failed. Please try uploading again.</Alert>
      )}

      {result.status === "checked" && (
        <>
          <div className={`my-4 overflow-hidden rounded-2xl bg-gradient-to-br ${toneGrad[tone]} p-6 text-center text-white shadow-card`}>
            <p className="text-sm font-medium text-white/80">Total Marks</p>
            <p className="mt-1 text-4xl font-bold tracking-tight">
              {result.total_marks_awarded}
              <span className="text-xl font-medium text-white/70"> / {result.total_marks_possible}</span>
            </p>
            {pct != null && <p className="mt-1 text-sm font-medium text-white/80">{pct}%</p>}
            {result.checked_pdf_url && (
              <a
                href={result.checked_pdf_url}
                target="_blank"
                rel="noreferrer"
                className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-white/20 px-4 py-2 text-sm font-semibold text-white backdrop-blur-sm transition-colors hover:bg-white/30"
              >
                <ExternalLink className="h-4 w-4" /> View Checked PDF
              </a>
            )}
          </div>

          <div className="space-y-3">
            {result.questions.map((q) => (
              <Card key={q.question_number}>
                <div className="flex items-center justify-between">
                  <span className="font-semibold text-gray-800">प्रश्न {q.question_number}</span>
                  <Badge tone="brand" className="text-sm">{q.marks_awarded} / {q.marks_possible}</Badge>
                </div>
                {q.question_text && <p className="mt-1 text-xs text-gray-500 font-deva">{q.question_text}</p>}
                {q.sections && q.sections.length > 0 && (
                  <div className="mt-3">
                    <SectionBreakdown sections={q.sections} />
                  </div>
                )}
                {q.feedback && (
                  <div className="mt-3 rounded-xl bg-gray-50/70 p-3">
                    <RichText size="sm">{q.feedback}</RichText>
                  </div>
                )}
                {q.mistakes.length > 0 && (
                  <div className="mt-2">
                    <p className="mb-1 text-xs font-semibold text-danger-600">सुधार्नुपर्ने बुँदा</p>
                    <ul className="space-y-1">
                      {q.mistakes.map((m, i) => (
                        <li key={i} className="flex gap-1.5 text-xs text-danger-600 font-deva">
                          <span className="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-danger-400" />
                          {m}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Card>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
