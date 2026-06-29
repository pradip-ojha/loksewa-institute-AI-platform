import { useCallback, useEffect, useRef, useState } from "react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { subjectiveTestsService } from "../../services/subjectiveTests";
import type {
  SubjectiveTest, SubjectiveTestDetail, Submission,
} from "../../services/subjectiveTests";
import { getErrorMessage } from "../../utils/error";
import { SubjectiveAnalyticsView } from "./Analytics";
import { useExam } from "../../context/ExamContext";

type Tab = "create" | "tests" | "submissions" | "analytics";

const STATUS_BADGE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  active: "bg-green-100 text-green-700",
  archived: "bg-gray-200 text-gray-500",
};
const SKILL_BADGE: Record<string, string> = {
  pending: "bg-gray-100 text-gray-600",
  processing: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const TABS: { key: Tab; label: string }[] = [
  { key: "create", label: "Create Test" },
  { key: "tests", label: "Test List" },
  { key: "submissions", label: "Submissions" },
  { key: "analytics", label: "Analytics" },
];

function Label({ children }: { children: React.ReactNode }) {
  return <label className="mb-1 block text-sm font-medium text-gray-700">{children}</label>;
}
function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none" />;
}
function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none" />;
}

export function AdminSubjectiveTests() {
  const [tab, setTab] = useState<Tab>("create");

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Subjective Tests</h1>
      <p className="mb-6 text-sm text-gray-500">
        Configure tests; the system extracts questions + marks and auto-generates per-question checking guides.
      </p>

      <div className="mb-6 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key ? "border-b-2 border-brand-600 text-brand-700" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "create" && <CreateTestTab onCreated={() => setTab("tests")} />}
      {tab === "tests" && <TestListTab />}
      {tab === "submissions" && <SubmissionsTab />}
      {tab === "analytics" && (
        <SubjectiveAnalyticsView />
      )}
    </div>
  );
}

// ── Create Test ─────────────────────────────────────────────────────────────────

function CreateTestTab({ onCreated }: { onCreated: () => void }) {
  const { selectedExamId } = useExam();
  const [displayName, setDisplayName] = useState("");
  const [totalTime, setTotalTime] = useState("60");
  const [totalMarks, setTotalMarks] = useState("");
  const [instruction, setInstruction] = useState("");
  const [modelHandwritten, setModelHandwritten] = useState(false);

  const paperRef = useRef<HTMLInputElement>(null);
  const modelRef = useRef<HTMLInputElement>(null);
  const sampleRef = useRef<HTMLInputElement>(null);
  const rubricRef = useRef<HTMLInputElement>(null);

  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!displayName.trim()) return setError("Display name is required.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    const paper = paperRef.current?.files?.[0];
    if (!paper) return setError("Question paper file is required.");

    const fd = new FormData();
    fd.append("display_name", displayName);
    fd.append("exam_id", selectedExamId);
    fd.append("total_time_minutes", totalTime || "60");
    fd.append("total_marks", totalMarks || "0");
    if (instruction.trim()) fd.append("custom_instruction", instruction);
    fd.append("question_paper", paper);
    const model = modelRef.current?.files?.[0];
    const sample = sampleRef.current?.files?.[0];
    const rubric = rubricRef.current?.files?.[0];
    if (model) fd.append("model_answer", model);
    if (model && modelHandwritten) fd.append("model_answer_is_handwritten", "true");
    if (sample) fd.append("sample_marked", sample);
    if (rubric) fd.append("rubric", rubric);

    setSubmitting(true);
    try {
      const job = await subjectiveTestsService.createTest(fd);
      setJobId(job.id);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to create test."));
    } finally {
      setSubmitting(false);
    }
  }

  function handleJobDone(_job: JobState) {
    // Test list reflects the new test; let the admin jump there.
  }

  return (
    <div className="max-w-2xl">
      <form onSubmit={handleSubmit} className="space-y-4 rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-100">
        <div>
          <Label>Display Name *</Label>
          <Input value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="e.g. Banking — Unit Test 1" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Total Time (minutes)</Label>
            <Input type="number" value={totalTime} onChange={(e) => setTotalTime(e.target.value)} />
          </div>
          <div>
            <Label>Total Marks (optional — derived from paper)</Label>
            <Input type="number" value={totalMarks} onChange={(e) => setTotalMarks(e.target.value)} />
          </div>
        </div>
        <div>
          <Label>Question Paper (PDF/Word) *</Label>
          <input ref={paperRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
          <p className="mt-1 text-xs text-gray-400">Must show clear question numbering + marks per question, e.g. "Q1 … [8 marks]".</p>
        </div>
        <div>
          <Label>Model / Ideal Answer (optional)</Label>
          <input ref={modelRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
          <label className="mt-2 flex items-center gap-2 text-xs text-gray-600">
            <input
              type="checkbox"
              checked={modelHandwritten}
              onChange={(e) => setModelHandwritten(e.target.checked)}
            />
            Model answer is handwritten (scanned) — use Gemini to read it; otherwise typed text is read by GPT-5.
          </label>
        </div>
        <div>
          <Label>Sample Marked Answer (optional)</Label>
          <input ref={sampleRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
        </div>
        <div>
          <Label>Marking Rubric (optional)</Label>
          <input ref={rubricRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
          <p className="mt-1 text-xs text-gray-400">If omitted, a sensible default general rubric is used.</p>
        </div>
        <div>
          <Label>Custom Checking Instruction (optional)</Label>
          <TextArea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Highest-priority guidance for the AI checker." />
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex items-center gap-3">
          <button type="submit" disabled={submitting} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
            {submitting ? "Creating…" : "Create Test"}
          </button>
          {jobId && (
            <button type="button" onClick={onCreated} className="text-sm text-brand-700 hover:underline">
              Go to Test List →
            </button>
          )}
        </div>
      </form>

      {jobId && (
        <JobStatusPoller jobId={jobId} className="mt-4" onComplete={handleJobDone} onFail={handleJobDone} />
      )}
    </div>
  );
}

// ── Test List ───────────────────────────────────────────────────────────────────

function TestListTab() {
  const [tests, setTests] = useState<SubjectiveTest[]>([]);
  const [detail, setDetail] = useState<SubjectiveTestDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await subjectiveTestsService.listTests(1, 50);
      setTests(res.items);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load tests."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function openDetail(id: string) {
    try {
      setDetail(await subjectiveTestsService.getTest(id));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load test."));
    }
  }
  async function activate(id: string) {
    try { await subjectiveTestsService.activateTest(id); await refresh(); }
    catch (err) { setError(getErrorMessage(err, "Could not activate.")); }
  }
  async function archive(id: string) {
    try { await subjectiveTestsService.archiveTest(id); await refresh(); }
    catch (err) { setError(getErrorMessage(err, "Could not archive.")); }
  }
  async function regenerate(id: string) {
    try { await subjectiveTestsService.regenerateSkills(id); await refresh(); }
    catch (err) { setError(getErrorMessage(err, "Could not regenerate skills.")); }
  }

  if (loading) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {tests.length === 0 ? (
        <p className="text-sm text-gray-500">No tests yet. Create one from the Create Test tab.</p>
      ) : (
        <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
              <tr>
                <th className="px-4 py-3">Test</th>
                <th className="px-4 py-3">Questions</th>
                <th className="px-4 py-3">Marks</th>
                <th className="px-4 py-3">Skills</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {tests.map((t) => (
                <tr key={t.id}>
                  <td className="px-4 py-3 font-medium text-gray-800">{t.display_name}</td>
                  <td className="px-4 py-3">{t.num_questions}</td>
                  <td className="px-4 py-3">{t.total_marks}</td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${SKILL_BADGE[t.skill_generation_status] ?? ""}`}>
                      {t.skill_generation_status}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`rounded-full px-2 py-0.5 text-xs ${STATUS_BADGE[t.status] ?? ""}`}>{t.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => openDetail(t.id)} className="mr-3 text-brand-700 hover:underline">View</button>
                    {t.status !== "active" && t.skill_generation_status === "completed" && (
                      <button onClick={() => activate(t.id)} className="mr-3 text-green-700 hover:underline">Activate</button>
                    )}
                    {t.skill_generation_status !== "processing" && (
                      <button onClick={() => regenerate(t.id)} className="mr-3 text-amber-700 hover:underline">Regenerate skills</button>
                    )}
                    {t.status === "active" && (
                      <button onClick={() => archive(t.id)} className="text-gray-500 hover:underline">Archive</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detail && (
        <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4" onClick={() => setDetail(null)}>
          <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-xl bg-white p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-semibold">{detail.display_name}</h3>
              <button onClick={() => setDetail(null)} className="text-gray-400 hover:text-gray-700">✕</button>
            </div>
            <div className="mb-3 flex flex-wrap gap-3 text-sm">
              {detail.question_paper_url && <a className="text-brand-700 hover:underline" href={detail.question_paper_url} target="_blank" rel="noreferrer">Question Paper</a>}
              {detail.model_answer_url && <a className="text-brand-700 hover:underline" href={detail.model_answer_url} target="_blank" rel="noreferrer">Model Answer</a>}
              {detail.rubric_url && <a className="text-brand-700 hover:underline" href={detail.rubric_url} target="_blank" rel="noreferrer">Rubric</a>}
            </div>
            <ol className="space-y-3">
              {detail.questions.map((q) => (
                <li key={q.id} className="rounded-lg border border-gray-100 p-3">
                  <div className="flex justify-between text-sm font-medium text-gray-800">
                    <span>{q.question_number}</span>
                    <span className="text-gray-500">{q.marks} marks</span>
                  </div>
                  <p className="mt-1 text-sm text-gray-600">{q.question_text}</p>
                  {(q.topic || q.subtopic) && (
                    <p className="mt-1 text-xs text-gray-400">
                      {[q.topic, q.subtopic].filter(Boolean).join(" › ")}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Submissions ─────────────────────────────────────────────────────────────────

function SubmissionsTab() {
  const [tests, setTests] = useState<SubjectiveTest[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [rows, setRows] = useState<Submission[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    subjectiveTestsService.listTests(1, 100).then((r) => setTests(r.items)).catch(() => undefined);
  }, []);

  const load = useCallback(async (id: string) => {
    if (!id) return;
    try {
      setRows(await subjectiveTestsService.listSubmissions(id));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load submissions."));
    }
  }, []);

  useEffect(() => { if (selected) void load(selected); }, [selected, load]);

  return (
    <div>
      <div className="mb-4 max-w-sm">
        <Label>Select Test</Label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
        >
          <option value="">— choose —</option>
          {tests.map((t) => <option key={t.id} value={t.id}>{t.display_name}</option>)}
        </select>
      </div>

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {selected && (
        rows.length === 0 ? (
          <p className="text-sm text-gray-500">No submissions yet.</p>
        ) : (
          <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left text-xs uppercase text-gray-500">
                <tr>
                  <th className="px-4 py-3">Student</th>
                  <th className="px-4 py-3">Attempt</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Marks</th>
                  <th className="px-4 py-3">Checked PDF</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {rows.map((r) => (
                  <tr key={r.sheet_id}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-800">{r.student_name}</div>
                      <div className="text-xs text-gray-400">{r.student_email}</div>
                    </td>
                    <td className="px-4 py-3">{r.upload_attempt_number}</td>
                    <td className="px-4 py-3 capitalize">{r.current_status}</td>
                    <td className="px-4 py-3">
                      {r.total_marks_awarded != null ? `${r.total_marks_awarded} / ${r.total_marks_possible}` : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {r.checked_pdf_url ? (
                        <a className="text-brand-700 hover:underline" href={r.checked_pdf_url} target="_blank" rel="noreferrer">Open</a>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}
