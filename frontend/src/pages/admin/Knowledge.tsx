import { useEffect, useMemo, useRef, useState } from "react";
import { BookOpen, UploadCloud } from "lucide-react";
import { knowledgeService, type KnowledgeChunk, type KnowledgeDocument } from "../../services/knowledge";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";
import { JobStatusPoller, type JobState } from "../../components/JobStatusPoller";
import { useExam } from "../../context/ExamContext";
import {
  PageHeader,
  Tabs,
  Button,
  Alert,
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
  useToast,
  type Column,
} from "../../components/ui";

const DOC_TYPES = [
  { value: "notes", label: "Notes" },
  { value: "book_content", label: "Book Content" },
  { value: "handout", label: "Handout" },
  { value: "reference_material", label: "Reference Material" },
  { value: "model_qa", label: "Model Q&A" },
];

type Tab = "upload" | "processed" | "logs";

const TABS = [
  { id: "upload", label: "Upload Knowledge" },
  { id: "processed", label: "Processed Knowledge" },
  { id: "logs", label: "Processing Logs" },
];

export function AdminKnowledge() {
  const [tab, setTab] = useState<Tab>("upload");

  return (
    <div>
      <PageHeader
        title="Knowledge Layer"
        description="Upload and manage knowledge documents for AI workflows."
        icon={<BookOpen className="h-5 w-5" />}
      />

      <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} className="mb-6" />

      {tab === "upload" && <UploadTab />}
      {tab === "processed" && <ProcessedTab />}
      {tab === "logs" && <LogsTab />}
    </div>
  );
}

// ── Upload Tab ───────────────────────────────────────────────────────────────

function UploadTab() {
  // Choosing an exam here has PRIORITY: it updates the global selection (the top-bar
  // selector), so what you pick while uploading also becomes the scope for the Processed /
  // Logs tabs. This avoids uploading against a stale top-bar exam.
  const { exams, selectedExamId, setSelectedExamId } = useExam();
  const fileRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState({
    display_name: "",
    document_type: "notes",
    chapter: "",
    topic: "",
    subtopic: "",
    custom_instruction: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [docId, setDocId] = useState<string | null>(null);

  const [tree, setTree] = useState<SyllabusTree | null>(null);

  useEffect(() => {
    if (!selectedExamId) {
      setTree(null);
      return;
    }
    syllabusService.get(selectedExamId).then(setTree).catch(() => setTree(null));
  }, [selectedExamId]);

  const availableChapters = useMemo(() => tree?.chapters ?? [], [tree]);

  const availableTopics = useMemo(() => {
    if (form.chapter) return availableChapters.find((c) => c.chapter === form.chapter)?.topics ?? [];
    return availableChapters.flatMap((c) => c.topics);
  }, [form.chapter, availableChapters]);

  const availableSubtopics = useMemo(() => availableTopics.find((t) => t.topic === form.topic)?.subtopics ?? [], [form.topic, availableTopics]);

  const set = (field: string, value: string) => setForm((f) => ({ ...f, [field]: value }));

  const handleChapterChange = (value: string) => {
    setForm((f) => ({ ...f, chapter: value, topic: "", subtopic: "" }));
  };

  const handleTopicChange = (value: string) => {
    setForm((f) => ({ ...f, topic: value, subtopic: "" }));
  };

  const handleSubmit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!selectedExamId) {
      setError("Select an exam in the top bar first.");
      return;
    }
    if (!file) {
      setError("Select a file to upload.");
      return;
    }
    if (!form.display_name.trim()) {
      setError("Display name is required.");
      return;
    }

    setSubmitting(true);
    setError(null);
    setJobId(null);
    setDocId(null);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("display_name", form.display_name);
    fd.append("document_type", form.document_type);
    fd.append("exam_id", selectedExamId);
    if (form.chapter) fd.append("chapter", form.chapter);
    if (form.topic) fd.append("topic", form.topic);
    if (form.subtopic) fd.append("subtopic", form.subtopic);
    if (form.custom_instruction) fd.append("custom_instruction", form.custom_instruction);

    try {
      const result = await knowledgeService.upload(fd);
      setJobId(result.job_id);
      setDocId(result.id);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Upload failed.";
      setError(String(msg));
    } finally {
      setSubmitting(false);
    }
  };

  const handleJobDone = (_job: JobState) => {};

  return (
    <div className="max-w-3xl rounded-lg border border-gray-200 bg-white p-6">
      <h3 className="mb-5 text-sm font-semibold text-gray-900">Upload Knowledge Document</h3>

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Exam" required className="sm:col-span-2" hint="Changing this also updates the exam selected in the top bar (used across the admin workspace, including the Processed Knowledge and Logs tabs).">
          <Select value={selectedExamId ?? ""} onChange={(e) => { if (e.target.value) setSelectedExamId(e.target.value); }}>
            {!selectedExamId && <option value="">— Select an exam —</option>}
            {exams.map((ex) => (
              <option key={ex.id} value={ex.id}>
                {ex.name} ({ex.exam_type})
              </option>
            ))}
          </Select>
        </FormField>

        <FormField label="Document Display Name" required className="sm:col-span-2">
          <TextInput value={form.display_name} onChange={(e) => set("display_name", e.target.value)} placeholder="e.g. RBB Objective Notes Chapter 1" />
        </FormField>

        <FormField
          label="Document Type"
          required
          hint={form.document_type === "model_qa" ? "Upload a question paper with model answers. Each question–answer pair is stored separately and retrieved by its question." : undefined}
        >
          <Select value={form.document_type} onChange={(e) => set("document_type", e.target.value)}>
            {DOC_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
        </FormField>

        <FormField label="Chapter (optional)" hint="Leave blank for a whole book or note set spanning multiple chapters — each chunk is auto-mapped to the syllabus. Pick a chapter only for single-chapter material.">
          <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
            <option value="">— Whole book / all chapters —</option>
            {availableChapters.map((c) => (
              <option key={c.chapter} value={c.chapter}>
                {c.chapter}
              </option>
            ))}
          </Select>
        </FormField>

        <FormField label="Topic (optional)">
          <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)}>
            <option value="">— Leave blank for auto-detection —</option>
            {availableTopics.map((t) => (
              <option key={t.topic} value={t.topic}>
                {t.topic}
              </option>
            ))}
          </Select>
        </FormField>

        <FormField label="Subtopic (optional)">
          {availableSubtopics.length > 0 ? (
            <Select value={form.subtopic} onChange={(e) => set("subtopic", e.target.value)}>
              <option value="">— Leave blank for auto-detection —</option>
              {availableSubtopics.map((s) => (
                <option key={s.id} value={s.subtopic}>
                  {s.subtopic}
                </option>
              ))}
            </Select>
          ) : (
            <Select value="" disabled>
              <option value="">{form.topic ? "No subtopics for this topic" : "Select a topic first"}</option>
            </Select>
          )}
        </FormField>

        <FormField label="Custom Instruction (optional)" className="sm:col-span-2">
          <Textarea value={form.custom_instruction} onChange={(e) => set("custom_instruction", e.target.value)} rows={3} placeholder="e.g. Focus on exam-relevant definitions and formulas only" />
        </FormField>

        <FormField label="Upload File (PDF or DOCX, max 50 MB)" required className="sm:col-span-2">
          <input ref={fileRef} type="file" accept=".pdf,.docx,.doc" className="text-sm text-gray-600" />
        </FormField>
      </div>

      {error && <Alert className="mt-4">{error}</Alert>}

      <Button className="mt-5" icon={<UploadCloud className="h-4 w-4" />} onClick={handleSubmit} loading={submitting}>
        Upload &amp; Process
      </Button>

      {jobId && (
        <div className="mt-6 space-y-2">
          <p className="text-sm font-medium text-gray-700">Processing…</p>
          <p className="text-xs text-gray-500">
            Document ID: <span className="font-mono">{docId}</span>
          </p>
          <JobStatusPoller jobId={jobId} onComplete={handleJobDone} onFail={handleJobDone} />
        </div>
      )}
    </div>
  );
}

// ── Processed Knowledge Tab ──────────────────────────────────────────────────

function ProcessedTab() {
  const { exams, selectedExamId } = useExam();
  const [filterExamId, setFilterExamId] = useState<string>(selectedExamId ?? "");
  useEffect(() => {
    setFilterExamId(selectedExamId ?? "");
  }, [selectedExamId]);

  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [chunks, setChunks] = useState<KnowledgeChunk[] | null>(null);
  const [chunksDocName, setChunksDocName] = useState<string>("");
  const [chunksLoading, setChunksLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [reprocessJobId, setReprocessJobId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      setDocs(await knowledgeService.list(filterExamId || undefined));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filterExamId]);

  const handleViewChunks = async (doc: KnowledgeDocument) => {
    setChunksLoading(true);
    setChunks(null);
    setChunksDocName(doc.display_name);
    try {
      setChunks(await knowledgeService.getChunks(doc.id));
    } finally {
      setChunksLoading(false);
    }
  };

  const doDelete = async () => {
    if (!confirmDel) return;
    const id = confirmDel;
    setConfirmDel(null);
    setDeletingId(id);
    try {
      await knowledgeService.delete(id);
      await load();
      if (chunks !== null) setChunks(null);
    } finally {
      setDeletingId(null);
    }
  };

  const handleReprocess = async (id: string) => {
    const job = await knowledgeService.reprocess(id);
    setReprocessJobId(job.id);
  };

  const columns: Column<KnowledgeDocument>[] = [
    {
      key: "display_name",
      header: "Name",
      accessor: (d) => d.display_name,
      render: (d) => <span className="block max-w-xs truncate font-medium text-gray-900">{d.display_name}</span>,
    },
    { key: "type", header: "Type", render: (d) => <span className="capitalize text-gray-500">{d.document_type.replace("_", " ")}</span> },
    { key: "chapter", header: "Chapter", render: (d) => <span className="text-gray-500">{d.chapter ?? "—"}</span> },
    { key: "chunks", header: "Chunks", align: "right", render: (d) => <span className="tabular-nums text-gray-700">{d.chunk_count}</span> },
    { key: "status", header: "Status", render: (d) => <StatusBadge status={d.processing_status} /> },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "3rem",
      render: (d) => (
        <Menu
          items={[
            { label: "View chunks", onClick: () => handleViewChunks(d) },
            { label: "Reprocess", onClick: () => handleReprocess(d.id) },
            { label: deletingId === d.id ? "Deleting…" : "Delete", tone: "danger", disabled: deletingId === d.id, onClick: () => setConfirmDel(d.id) },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium text-gray-600">Exam:</label>
        <div className="w-72">
          <Select value={filterExamId} onChange={(e) => setFilterExamId(e.target.value)}>
            <option value="">All exams</option>
            {exams.map((ex) => (
              <option key={ex.id} value={ex.id}>
                {ex.name} ({ex.exam_type})
              </option>
            ))}
          </Select>
        </div>
      </div>

      {reprocessJobId && (
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <p className="mb-2 text-sm font-medium text-gray-700">Reprocessing…</p>
          <JobStatusPoller
            jobId={reprocessJobId}
            onComplete={() => {
              setReprocessJobId(null);
              load();
            }}
            onFail={() => setReprocessJobId(null)}
          />
        </div>
      )}

      <DataTable
        columns={columns}
        rows={docs}
        rowKey={(d) => d.id}
        loading={loading}
        emptyState={
          <EmptyState
            icon={<BookOpen className="h-5 w-5" />}
            title={filterExamId ? "No documents for this exam" : "No documents yet"}
            description={filterExamId ? undefined : "Upload one from the Upload tab."}
            className="border-0"
          />
        }
      />

      {(chunks !== null || chunksLoading) && (
        <div className="rounded-lg border border-gray-200 bg-white p-5">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-900">Chunks — {chunksDocName}</h3>
            <Button variant="ghost" size="sm" onClick={() => setChunks(null)}>
              Close
            </Button>
          </div>

          {chunksLoading ? (
            <p className="text-sm text-gray-400">Loading chunks…</p>
          ) : chunks && chunks.length === 0 ? (
            <p className="text-sm text-gray-400">No chunks yet (document may still be processing).</p>
          ) : (
            <div className="max-h-96 space-y-3 overflow-y-auto scrollbar-thin">
              {chunks?.map((chunk) => (
                <div key={chunk.id} className="rounded-md bg-gray-50 p-3">
                  <div className="mb-2 flex flex-wrap gap-2 text-xs">
                    <Tag label="Type" value={chunk.content_type} />
                    <Tag label="Chapter" value={chunk.chapter} />
                    <Tag label="Topic" value={chunk.topic} />
                    {chunk.subtopic && <Tag label="Subtopic" value={chunk.subtopic} />}
                    <Tag label="Language" value={chunk.language} />
                  </div>
                  <p className="line-clamp-4 text-xs leading-relaxed text-gray-700">{chunk.content}</p>
                  {chunk.pinecone_vector_id && <p className="mt-1 truncate font-mono text-xs text-gray-400">vec: {chunk.pinecone_vector_id}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={!!confirmDel}
        title="Delete document"
        description="Delete this document and all its chunks from Pinecone? This cannot be undone."
        confirmLabel="Delete"
        tone="danger"
        onConfirm={doDelete}
        onClose={() => setConfirmDel(null)}
      />
    </div>
  );
}

// ── Processing Logs Tab ──────────────────────────────────────────────────────

interface JobRow {
  id: string;
  job_type: string;
  status: string;
  progress_percent: number;
  current_step: string | null;
  error_message: string | null;
  created_at: string;
}

function LogsTab() {
  const toast = useToast();
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);

  const load = () => {
    import("../../services/api").then(({ default: api }) => {
      api
        .get("/api/admin/jobs/knowledge")
        .then((r) => setJobs(r.data))
        .catch(() => {})
        .finally(() => setLoading(false));
    });
  };

  useEffect(() => {
    load();
  }, []);

  const doDelete = async () => {
    if (!confirmDel) return;
    const id = confirmDel;
    setConfirmDel(null);
    setDeletingId(id);
    try {
      const { default: api } = await import("../../services/api");
      await api.delete(`/api/admin/jobs/${id}`);
      load();
    } catch {
      toast.error("Could not delete the job. Please try again.");
    } finally {
      setDeletingId(null);
    }
  };

  const columns: Column<JobRow>[] = [
    { key: "job_type", header: "Type", accessor: (j) => j.job_type, render: (j) => <span className="text-gray-700">{j.job_type}</span> },
    { key: "status", header: "Status", render: (j) => <StatusBadge status={j.status} /> },
    { key: "progress", header: "Progress", align: "right", render: (j) => <span className="tabular-nums text-gray-700">{j.progress_percent}%</span> },
    { key: "step", header: "Step", render: (j) => <span className="block max-w-xs truncate text-gray-500">{j.current_step ?? "—"}</span> },
    { key: "created", header: "Created", render: (j) => <span className="tabular-nums text-gray-400">{new Date(j.created_at).toLocaleString()}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "6rem",
      render: (j) => (
        <Button variant="ghost" size="xs" className="text-danger-600 hover:bg-danger-50" disabled={deletingId === j.id} onClick={() => setConfirmDel(j.id)}>
          {deletingId === j.id ? "Deleting…" : "Delete"}
        </Button>
      ),
    },
  ];

  if (loading) return <PageLoader />;

  return (
    <>
      <DataTable
        columns={columns}
        rows={jobs}
        rowKey={(j) => j.id}
        emptyState={<EmptyState title="No processing jobs yet" className="border-0" />}
      />
      <ConfirmDialog
        open={!!confirmDel}
        title="Delete job"
        description="Delete this job? Use this only for a job you believe is stuck. The worker task is cancelled and the job removed; if content was mid-processing it will be marked failed so you can retry it. No finished content is lost."
        confirmLabel="Delete"
        tone="danger"
        onConfirm={doDelete}
        onClose={() => setConfirmDel(null)}
      />
    </>
  );
}

// ── Shared primitives ────────────────────────────────────────────────────────

function Tag({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <span className="rounded border border-gray-200 bg-white px-1.5 py-0.5">
      <span className="text-gray-400">{label}: </span>
      <span className="text-gray-700">{value}</span>
    </span>
  );
}
