import { useCallback, useEffect, useRef, useState } from "react";
import { FileCheck2 } from "lucide-react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import { subjectiveTestsService } from "../../services/subjectiveTests";
import type { SubjectiveTest, SubjectiveTestDetail, Submission } from "../../services/subjectiveTests";
import { getErrorMessage } from "../../utils/error";
import { SubjectiveAnalyticsView } from "./Analytics";
import { useExam } from "../../context/ExamContext";
import {
  PageHeader,
  Tabs,
  Button,
  Alert,
  Modal,
  FormField,
  TextInput,
  Textarea,
  Select,
  StatusBadge,
  DataTable,
  Menu,
  ConfirmDialog,
  EmptyState,
  PageLoader,
  type Column,
} from "../../components/ui";

type Tab = "create" | "tests" | "submissions" | "analytics";

const TABS = [
  { id: "create", label: "Create Test" },
  { id: "tests", label: "Test List" },
  { id: "submissions", label: "Submissions" },
  { id: "analytics", label: "Analytics" },
];

export function AdminSubjectiveTests() {
  const [tab, setTab] = useState<Tab>("create");

  return (
    <div>
      <PageHeader
        title="Subjective Tests"
        description="Configure tests; the system extracts questions + marks and auto-generates per-question checking guides."
        icon={<FileCheck2 className="h-5 w-5" />}
      />

      <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} className="mb-6" />

      {tab === "create" && <CreateTestTab onCreated={() => setTab("tests")} />}
      {tab === "tests" && <TestListTab />}
      {tab === "submissions" && <SubmissionsTab />}
      {tab === "analytics" && <SubjectiveAnalyticsView />}
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

  return (
    <div className="max-w-2xl">
      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-gray-200 bg-white p-6">
        <FormField label="Display Name" required>
          <TextInput value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="e.g. Banking — Unit Test 1" />
        </FormField>
        <div className="grid grid-cols-2 gap-4">
          <FormField label="Total Time (minutes)">
            <TextInput type="number" value={totalTime} onChange={(e) => setTotalTime(e.target.value)} />
          </FormField>
          <FormField label="Total Marks (optional — derived from paper)">
            <TextInput type="number" value={totalMarks} onChange={(e) => setTotalMarks(e.target.value)} />
          </FormField>
        </div>
        <FormField label="Question Paper (PDF/Word)" required hint='Must show clear question numbering + marks per question, e.g. "Q1 … [8 marks]".'>
          <input ref={paperRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
        </FormField>
        <FormField label="Model / Ideal Answer (optional)">
          <input ref={modelRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
          <label className="mt-2 flex items-center gap-2 text-xs text-gray-600">
            <input type="checkbox" checked={modelHandwritten} onChange={(e) => setModelHandwritten(e.target.checked)} />
            Model answer is handwritten (scanned) — use Gemini to read it; otherwise typed text is read by GPT-5.
          </label>
        </FormField>
        <FormField label="Sample Marked Answer (optional)">
          <input ref={sampleRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
        </FormField>
        <FormField label="Marking Rubric (optional)" hint="If omitted, a sensible default general rubric is used.">
          <input ref={rubricRef} type="file" accept=".pdf,.docx,.doc" className="text-sm" />
        </FormField>
        <FormField label="Custom Checking Instruction (optional)">
          <Textarea rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Highest-priority guidance for the AI checker." />
        </FormField>

        {error && <Alert>{error}</Alert>}

        <div className="flex items-center gap-3">
          <Button type="submit" loading={submitting}>
            Create Test
          </Button>
          {jobId && (
            <Button type="button" variant="ghost" onClick={onCreated}>
              Go to Test List →
            </Button>
          )}
        </div>
      </form>

      {jobId && <JobStatusPoller jobId={jobId} className="mt-4" onComplete={() => {}} onFail={() => {}} />}
    </div>
  );
}

// ── Test List ───────────────────────────────────────────────────────────────────

function TestListTab() {
  const [tests, setTests] = useState<SubjectiveTest[]>([]);
  const [detail, setDetail] = useState<SubjectiveTestDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [confirmDel, setConfirmDel] = useState<SubjectiveTest | null>(null);
  const [delBusy, setDelBusy] = useState(false);

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

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function openDetail(id: string) {
    try {
      setDetail(await subjectiveTestsService.getTest(id));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load test."));
    }
  }
  async function activate(id: string) {
    try {
      await subjectiveTestsService.activateTest(id);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not activate."));
    }
  }
  async function archive(id: string) {
    try {
      await subjectiveTestsService.archiveTest(id);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not archive."));
    }
  }
  async function regenerate(id: string) {
    try {
      await subjectiveTestsService.regenerateSkills(id);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not regenerate skills."));
    }
  }
  async function confirmRemove() {
    if (!confirmDel) return;
    setDelBusy(true);
    try {
      await subjectiveTestsService.deleteTest(confirmDel.id);
      setConfirmDel(null);
      await refresh();
    } catch (err) {
      setError(getErrorMessage(err, "Could not delete the test."));
    } finally {
      setDelBusy(false);
    }
  }

  const columns: Column<SubjectiveTest>[] = [
    { key: "display_name", header: "Test", accessor: (t) => t.display_name, render: (t) => <span className="font-medium text-gray-800">{t.display_name}</span> },
    { key: "num_questions", header: "Questions", align: "right", render: (t) => <span className="tabular-nums">{t.num_questions}</span> },
    { key: "total_marks", header: "Marks", align: "right", render: (t) => <span className="tabular-nums">{t.total_marks}</span> },
    { key: "skills", header: "Skills", render: (t) => <StatusBadge status={t.skill_generation_status} /> },
    { key: "status", header: "Status", render: (t) => <StatusBadge status={t.status} /> },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "3rem",
      render: (t) => (
        <Menu
          items={[
            { label: "View", onClick: () => openDetail(t.id) },
            ...(t.status !== "active" && t.skill_generation_status === "completed" ? [{ label: "Activate", onClick: () => activate(t.id) }] : []),
            ...(t.skill_generation_status !== "processing" ? [{ label: "Regenerate skills", onClick: () => regenerate(t.id) }] : []),
            ...(t.status === "active" ? [{ label: "Archive", onClick: () => archive(t.id) }] : []),
            { label: "Delete", tone: "danger" as const, onClick: () => setConfirmDel(t) },
          ]}
        />
      ),
    },
  ];

  if (loading) return <PageLoader />;

  return (
    <div>
      {error && <Alert className="mb-3">{error}</Alert>}
      <DataTable
        columns={columns}
        rows={tests}
        rowKey={(t) => t.id}
        emptyState={<EmptyState icon={<FileCheck2 className="h-5 w-5" />} title="No tests yet" description="Create one from the Create Test tab." className="border-0" />}
      />

      {detail && (
        <Modal open onClose={() => setDetail(null)} size="lg" title={detail.display_name}>
          <div className="mb-3 flex flex-wrap gap-3 text-sm">
            {detail.question_paper_url && (
              <a className="text-brand-600 hover:underline" href={detail.question_paper_url} target="_blank" rel="noreferrer">
                Question Paper
              </a>
            )}
            {detail.model_answer_url && (
              <a className="text-brand-600 hover:underline" href={detail.model_answer_url} target="_blank" rel="noreferrer">
                Model Answer
              </a>
            )}
            {detail.rubric_url && (
              <a className="text-brand-600 hover:underline" href={detail.rubric_url} target="_blank" rel="noreferrer">
                Rubric
              </a>
            )}
          </div>
          <ol className="space-y-3">
            {detail.questions.map((q) => (
              <li key={q.id} className="rounded-md border border-gray-200 p-3">
                <div className="flex justify-between text-sm font-medium text-gray-800">
                  <span>{q.question_number}</span>
                  <span className="text-gray-500 tabular-nums">{q.marks} marks</span>
                </div>
                <p className="mt-1 text-sm text-gray-600">{q.question_text}</p>
                {(q.topic || q.subtopic) && (
                  <p className="mt-1 text-xs text-gray-400">{[q.topic, q.subtopic].filter(Boolean).join(" › ")}</p>
                )}
              </li>
            ))}
          </ol>
        </Modal>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        title="Delete test"
        description={
          confirmDel
            ? `Permanently delete "${confirmDel.display_name}"? This removes the test, its questions, checking skills, every student submission + checked PDF, and its files. This cannot be undone.`
            : ""
        }
        confirmLabel="Delete"
        tone="danger"
        loading={delBusy}
        onConfirm={confirmRemove}
        onClose={() => setConfirmDel(null)}
      />
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
    subjectiveTestsService
      .listTests(1, 100)
      .then((r) => setTests(r.items))
      .catch(() => undefined);
  }, []);

  const load = useCallback(async (id: string) => {
    if (!id) return;
    try {
      setRows(await subjectiveTestsService.listSubmissions(id));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load submissions."));
    }
  }, []);

  useEffect(() => {
    if (selected) void load(selected);
  }, [selected, load]);

  const columns: Column<Submission>[] = [
    {
      key: "student",
      header: "Student",
      render: (r) => (
        <div>
          <div className="font-medium text-gray-800">{r.student_name}</div>
          <div className="text-xs text-gray-400">{r.student_email}</div>
        </div>
      ),
    },
    { key: "attempt", header: "Attempt", align: "right", render: (r) => <span className="tabular-nums">{r.upload_attempt_number}</span> },
    { key: "status", header: "Status", render: (r) => <StatusBadge status={r.current_status} /> },
    {
      key: "marks",
      header: "Marks",
      align: "right",
      render: (r) => (
        <span className="tabular-nums">{r.total_marks_awarded != null ? `${r.total_marks_awarded} / ${r.total_marks_possible}` : "—"}</span>
      ),
    },
    {
      key: "pdf",
      header: "Checked PDF",
      render: (r) =>
        r.checked_pdf_url ? (
          <a className="text-brand-600 hover:underline" href={r.checked_pdf_url} target="_blank" rel="noreferrer">
            Open
          </a>
        ) : (
          "—"
        ),
    },
  ];

  return (
    <div>
      <div className="mb-4 max-w-sm">
        <FormField label="Select Test">
          <Select value={selected} onChange={(e) => setSelected(e.target.value)}>
            <option value="">— choose —</option>
            {tests.map((t) => (
              <option key={t.id} value={t.id}>
                {t.display_name}
              </option>
            ))}
          </Select>
        </FormField>
      </div>

      {error && <Alert className="mb-3">{error}</Alert>}

      {selected && (
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(r) => r.sheet_id}
          emptyState={<EmptyState title="No submissions yet" className="border-0" />}
        />
      )}
    </div>
  );
}
