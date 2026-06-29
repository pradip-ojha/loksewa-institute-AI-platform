import { useEffect, useMemo, useRef, useState } from "react";
import { knowledgeService, type KnowledgeChunk, type KnowledgeDocument } from "../../services/knowledge";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";
import { JobStatusPoller, type JobState } from "../../components/JobStatusPoller";
import { useExam } from "../../context/ExamContext";

const DOC_TYPES = [
  { value: "notes", label: "Notes" },
  { value: "book_content", label: "Book Content" },
  { value: "handout", label: "Handout" },
  { value: "reference_material", label: "Reference Material" },
];

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-gray-100 text-gray-600",
  processing: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

type Tab = "upload" | "processed" | "logs";

export function AdminKnowledge() {
  const [tab, setTab] = useState<Tab>("upload");

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-gray-900">Knowledge Layer</h2>
        <p className="mt-1 text-sm text-gray-500">Upload and manage knowledge documents for AI workflows.</p>
      </div>

      <div className="mb-6 flex gap-1 rounded-xl bg-gray-100 p-1">
        {(["upload", "processed", "logs"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg py-2 text-sm font-medium capitalize transition-colors ${
              tab === t ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t === "processed" ? "Processed Knowledge" : t === "logs" ? "Processing Logs" : "Upload Knowledge"}
          </button>
        ))}
      </div>

      {tab === "upload" && <UploadTab />}
      {tab === "processed" && <ProcessedTab />}
      {tab === "logs" && <LogsTab />}
    </div>
  );
}

// ── Upload Tab ───────────────────────────────────────────────────────────────

function UploadTab() {
  const { selectedExamId, selectedExam } = useExam();
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
    if (!selectedExamId) { setTree(null); return; }
    syllabusService.get(selectedExamId).then(setTree).catch(() => setTree(null));
  }, [selectedExamId]);

  const availableChapters = useMemo(() => tree?.chapters ?? [], [tree]);

  const availableTopics = useMemo(() => {
    if (form.chapter) return availableChapters.find((c) => c.chapter === form.chapter)?.topics ?? [];
    return availableChapters.flatMap((c) => c.topics);
  }, [form.chapter, availableChapters]);

  const availableSubtopics = useMemo(
    () => availableTopics.find((t) => t.topic === form.topic)?.subtopics ?? [],
    [form.topic, availableTopics]
  );

  const set = (field: string, value: string) =>
    setForm((f) => ({ ...f, [field]: value }));

  const handleChapterChange = (value: string) => {
    setForm((f) => ({ ...f, chapter: value, topic: "", subtopic: "" }));
  };

  const handleTopicChange = (value: string) => {
    setForm((f) => ({ ...f, topic: value, subtopic: "" }));
  };

  const handleSubmit = async () => {
    const file = fileRef.current?.files?.[0];
    if (!selectedExamId) { setError("Select an exam in the top bar first."); return; }
    if (!file) { setError("Select a file to upload."); return; }
    if (!form.display_name.trim()) { setError("Display name is required."); return; }

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
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-100">
      <h3 className="mb-5 font-semibold text-gray-800">Upload Knowledge Document</h3>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label>Document Display Name *</Label>
          <Input
            value={form.display_name}
            onChange={(e) => set("display_name", e.target.value)}
            placeholder="e.g. RBB Objective Notes Chapter 1"
          />
        </div>

        <div>
          <Label>Document Type *</Label>
          <Select value={form.document_type} onChange={(e) => set("document_type", e.target.value)}>
            {DOC_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </Select>
        </div>

        <div>
          <Label>Chapter <span className="font-normal text-gray-400">(optional)</span></Label>
          <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
            <option value="">— All / unspecified —</option>
            {availableChapters.map((c) => (
              <option key={c.chapter} value={c.chapter}>{c.chapter}</option>
            ))}
          </Select>
          <p className="mt-1 text-xs text-gray-400">
            Exam: <span className="font-medium text-gray-600">{selectedExam?.name ?? "none selected"}</span>
          </p>
        </div>

        <div>
          <Label>Topic <span className="font-normal text-gray-400">(optional)</span></Label>
          <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)}>
            <option value="">— Leave blank for auto-detection —</option>
            {availableTopics.map((t) => (
              <option key={t.topic} value={t.topic}>{t.topic}</option>
            ))}
          </Select>
        </div>

        <div>
          <Label>Subtopic <span className="font-normal text-gray-400">(optional)</span></Label>
          {availableSubtopics.length > 0 ? (
            <Select value={form.subtopic} onChange={(e) => set("subtopic", e.target.value)}>
              <option value="">— Leave blank for auto-detection —</option>
              {availableSubtopics.map((s) => (
                <option key={s.id} value={s.subtopic}>{s.subtopic}</option>
              ))}
            </Select>
          ) : (
            <Select value="" disabled>
              <option value="">
                {form.topic ? "No subtopics for this topic" : "Select a topic first"}
              </option>
            </Select>
          )}
        </div>

        <div className="sm:col-span-2">
          <Label>Custom Instruction <span className="font-normal text-gray-400">(optional)</span></Label>
          <textarea
            value={form.custom_instruction}
            onChange={(e) => set("custom_instruction", e.target.value)}
            rows={3}
            placeholder="e.g. Focus on exam-relevant definitions and formulas only"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
          />
        </div>

        <div className="sm:col-span-2">
          <Label>Upload File * <span className="font-normal text-gray-400">(PDF or DOCX, max 50 MB)</span></Label>
          <input ref={fileRef} type="file" accept=".pdf,.docx,.doc" className="text-sm text-gray-600" />
        </div>
      </div>

      {error && (
        <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <button
        onClick={handleSubmit}
        disabled={submitting}
        className="mt-5 rounded-lg bg-brand-500 px-6 py-2.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
      >
        {submitting ? "Uploading…" : "Upload & Process"}
      </button>

      {jobId && (
        <div className="mt-6 space-y-2">
          <p className="text-sm font-medium text-gray-700">Processing…</p>
          <p className="text-xs text-gray-500">Document ID: <span className="font-mono">{docId}</span></p>
          <JobStatusPoller jobId={jobId} onComplete={handleJobDone} onFail={handleJobDone} />
        </div>
      )}
    </div>
  );
}

// ── Processed Knowledge Tab ──────────────────────────────────────────────────

function ProcessedTab() {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [chunks, setChunks] = useState<KnowledgeChunk[] | null>(null);
  const [chunksDocName, setChunksDocName] = useState<string>("");
  const [chunksLoading, setChunksLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [reprocessJobId, setReprocessJobId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try { setDocs(await knowledgeService.list()); } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const handleViewChunks = async (doc: KnowledgeDocument) => {
    setChunksLoading(true);
    setChunks(null);
    setChunksDocName(doc.display_name);
    try {
      setChunks(await knowledgeService.getChunks(doc.id));
    } finally { setChunksLoading(false); }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this document and all its chunks from Pinecone?")) return;
    setDeletingId(id);
    try {
      await knowledgeService.delete(id);
      await load();
      if (chunks !== null) setChunks(null);
    } finally { setDeletingId(null); }
  };

  const handleReprocess = async (id: string) => {
    const job = await knowledgeService.reprocess(id);
    setReprocessJobId(job.id);
  };

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;

  if (docs.length === 0)
    return (
      <div className="rounded-xl bg-white p-8 text-center shadow-sm ring-1 ring-gray-100">
        <p className="text-sm text-gray-400">No documents yet. Upload one from the Upload tab.</p>
      </div>
    );

  return (
    <div className="space-y-4">
      {reprocessJobId && (
        <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
          <p className="mb-2 text-sm font-medium text-gray-700">Reprocessing…</p>
          <JobStatusPoller
            jobId={reprocessJobId}
            onComplete={() => { setReprocessJobId(null); load(); }}
            onFail={() => setReprocessJobId(null)}
          />
        </div>
      )}

      <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <table className="w-full text-sm">
          <thead className="border-b border-gray-100 bg-gray-50 text-xs uppercase text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">Name</th>
              <th className="px-4 py-3 text-left">Type</th>
              <th className="px-4 py-3 text-left">Chapter</th>
              <th className="px-4 py-3 text-left">Chunks</th>
              <th className="px-4 py-3 text-left">Status</th>
              <th className="px-4 py-3 text-left">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {docs.map((doc) => (
              <tr key={doc.id} className="hover:bg-gray-50">
                <td className="px-4 py-3 font-medium text-gray-900 max-w-xs truncate">{doc.display_name}</td>
                <td className="px-4 py-3 text-gray-500 capitalize">{doc.document_type.replace("_", " ")}</td>
                <td className="px-4 py-3 text-gray-500">{doc.chapter ?? "—"}</td>
                <td className="px-4 py-3 text-gray-700">{doc.chunk_count}</td>
                <td className="px-4 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[doc.processing_status] ?? STATUS_COLORS.pending}`}>
                    {doc.processing_status}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleViewChunks(doc)}
                      className="text-xs font-medium text-brand-600 hover:text-brand-800"
                    >
                      View Chunks
                    </button>
                    <button
                      onClick={() => handleReprocess(doc.id)}
                      className="text-xs font-medium text-gray-500 hover:text-gray-800"
                    >
                      Reprocess
                    </button>
                    <button
                      onClick={() => handleDelete(doc.id)}
                      disabled={deletingId === doc.id}
                      className="text-xs font-medium text-red-500 hover:text-red-700 disabled:opacity-40"
                    >
                      {deletingId === doc.id ? "Deleting…" : "Delete"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(chunks !== null || chunksLoading) && (
        <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="font-semibold text-gray-800">
              Chunks — {chunksDocName}
            </h3>
            <button onClick={() => setChunks(null)} className="text-sm text-gray-400 hover:text-gray-600">
              Close
            </button>
          </div>

          {chunksLoading ? (
            <p className="text-sm text-gray-400">Loading chunks…</p>
          ) : chunks && chunks.length === 0 ? (
            <p className="text-sm text-gray-400">No chunks yet (document may still be processing).</p>
          ) : (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {chunks?.map((chunk) => (
                <div key={chunk.id} className="rounded-lg bg-gray-50 p-3">
                  <div className="mb-2 flex flex-wrap gap-2 text-xs">
                    <Tag label="Type" value={chunk.content_type} />
                    <Tag label="Chapter" value={chunk.chapter} />
                    <Tag label="Topic" value={chunk.topic} />
                    {chunk.subtopic && <Tag label="Subtopic" value={chunk.subtopic} />}
                    <Tag label="Language" value={chunk.language} />
                  </div>
                  <p className="text-xs text-gray-700 leading-relaxed line-clamp-4">{chunk.content}</p>
                  {chunk.pinecone_vector_id && (
                    <p className="mt-1 text-xs text-gray-400 font-mono truncate">vec: {chunk.pinecone_vector_id}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Processing Logs Tab ──────────────────────────────────────────────────────

function LogsTab() {
  const [jobs, setJobs] = useState<Array<{
    id: string; job_type: string; status: string;
    progress_percent: number; current_step: string | null;
    error_message: string | null; created_at: string;
  }>>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    import("../../services/api").then(({ default: api }) => {
      api.get("/api/admin/jobs/knowledge")
        .then((r) => setJobs(r.data))
        .catch(() => {})
        .finally(() => setLoading(false));
    });
  }, []);

  if (loading) return <p className="text-sm text-gray-400">Loading…</p>;

  if (jobs.length === 0)
    return (
      <div className="rounded-xl bg-white p-8 text-center shadow-sm ring-1 ring-gray-100">
        <p className="text-sm text-gray-400">No processing jobs yet.</p>
      </div>
    );

  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
      <table className="w-full text-sm">
        <thead className="border-b border-gray-100 bg-gray-50 text-xs uppercase text-gray-500">
          <tr>
            <th className="px-4 py-3 text-left">Type</th>
            <th className="px-4 py-3 text-left">Status</th>
            <th className="px-4 py-3 text-left">Progress</th>
            <th className="px-4 py-3 text-left">Step</th>
            <th className="px-4 py-3 text-left">Created</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">
          {jobs.map((job) => (
            <tr key={job.id} className="hover:bg-gray-50">
              <td className="px-4 py-3 text-gray-700">{job.job_type}</td>
              <td className="px-4 py-3">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[job.status] ?? STATUS_COLORS.pending}`}>
                  {job.status}
                </span>
              </td>
              <td className="px-4 py-3 text-gray-700">{job.progress_percent}%</td>
              <td className="px-4 py-3 text-gray-500 max-w-xs truncate">{job.current_step ?? "—"}</td>
              <td className="px-4 py-3 text-gray-400">{new Date(job.created_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Shared primitives ────────────────────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return <label className="mb-1 block text-sm font-medium text-gray-700">{children}</label>;
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
    />
  );
}

function Select({ children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
    >
      {children}
    </select>
  );
}

function Tag({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null;
  return (
    <span className="rounded bg-white px-1.5 py-0.5 ring-1 ring-gray-200">
      <span className="text-gray-400">{label}: </span>
      <span className="text-gray-700">{value}</span>
    </span>
  );
}
