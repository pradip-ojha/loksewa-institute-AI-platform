import { useEffect, useRef, useState } from "react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { mcqService } from "../../services/mcq";
import type { MCQQuestion, MCQReviewBatch, MCQBatchWithQuestions, MCQOption } from "../../services/mcq";

type Tab = "upload" | "generate" | "batches" | "review" | "bank" | "manual";

const COMPLEXITY_BADGE: Record<string, string> = {
  easy: "bg-green-100 text-green-700",
  medium: "bg-yellow-100 text-yellow-700",
  hard: "bg-red-100 text-red-700",
};
const STATUS_BADGE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  approved: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700",
  archived: "bg-gray-200 text-gray-500",
};

// ── Upload Existing MCQ Tab ───────────────────────────────────────────────────

function UploadTab({ onJobStart }: { onJobStart: (jobId: string, batchMode: "extract") => void }) {
  const [form, setForm] = useState({ display_name: "", topic: "", subtopic: "", custom_instruction: "" });
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) { setError("Please select a file."); return; }
    if (!form.display_name.trim()) { setError("Document name is required."); return; }
    setLoading(true); setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("display_name", form.display_name);
      fd.append("topic", form.topic);
      fd.append("subtopic", form.subtopic);
      fd.append("custom_instruction", form.custom_instruction);
      const result = await mcqService.uploadDocument(fd);
      onJobStart(result.job_id, "extract");
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || "Upload failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Document Display Name *</label>
        <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.display_name} onChange={e => setForm(p => ({ ...p, display_name: e.target.value }))} placeholder="e.g. Banking MCQ Set 2024" />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">PDF or Word File *</label>
        <input type="file" accept=".pdf,.doc,.docx" className="w-full text-sm" onChange={e => setFile(e.target.files?.[0] || null)} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Topic (optional)</label>
          <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.topic} onChange={e => setForm(p => ({ ...p, topic: e.target.value }))} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Subtopic (optional)</label>
          <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.subtopic} onChange={e => setForm(p => ({ ...p, subtopic: e.target.value }))} />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Custom Extraction Instruction (optional)</label>
        <textarea rows={3} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.custom_instruction} onChange={e => setForm(p => ({ ...p, custom_instruction: e.target.value }))} placeholder="e.g. Focus on questions about monetary policy" />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={loading} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {loading ? "Uploading…" : "Extract MCQs"}
      </button>
    </form>
  );
}

// ── Generate from Content Tab ─────────────────────────────────────────────────

function GenerateTab({ onJobStart }: { onJobStart: (jobId: string, mode: "generate") => void }) {
  const [form, setForm] = useState({ display_name: "", topic: "", subtopic: "", custom_instruction: "", count: "10" });
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) { setError("Please select a file."); return; }
    if (!form.display_name.trim()) { setError("Document name is required."); return; }
    setLoading(true); setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("display_name", form.display_name);
      fd.append("count", form.count);
      fd.append("topic", form.topic);
      fd.append("subtopic", form.subtopic);
      fd.append("custom_instruction", form.custom_instruction);
      const result = await mcqService.generateFromContent(fd);
      onJobStart(result.job_id, "generate");
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || "Generation failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Content Display Name *</label>
        <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.display_name} onChange={e => setForm(p => ({ ...p, display_name: e.target.value }))} placeholder="e.g. Banking Notes Chapter 3" />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Content File (PDF or Word) *</label>
        <input type="file" accept=".pdf,.doc,.docx" className="w-full text-sm" onChange={e => setFile(e.target.files?.[0] || null)} />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Number of MCQs to Generate</label>
        <input type="number" min={1} max={100} className="w-32 rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.count} onChange={e => setForm(p => ({ ...p, count: e.target.value }))} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Topic (optional)</label>
          <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.topic} onChange={e => setForm(p => ({ ...p, topic: e.target.value }))} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Subtopic (optional)</label>
          <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.subtopic} onChange={e => setForm(p => ({ ...p, subtopic: e.target.value }))} />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Custom Instruction (optional)</label>
        <textarea rows={3} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.custom_instruction} onChange={e => setForm(p => ({ ...p, custom_instruction: e.target.value }))} />
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={loading} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {loading ? "Submitting…" : "Generate MCQs"}
      </button>
    </form>
  );
}

// ── Question Card ─────────────────────────────────────────────────────────────

function QuestionCard({
  question,
  batchId,
  onUpdate,
}: {
  question: MCQQuestion;
  batchId: string;
  onUpdate: (q: MCQQuestion) => void;
}) {
  const [rejectFeedback, setRejectFeedback] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [loading, setLoading] = useState(false);

  async function accept() {
    setLoading(true);
    try {
      const updated = await mcqService.acceptQuestion(batchId, question.id);
      onUpdate(updated);
    } finally { setLoading(false); }
  }

  async function reject() {
    if (!rejectFeedback.trim()) return;
    setLoading(true);
    try {
      const updated = await mcqService.rejectQuestion(batchId, question.id, rejectFeedback);
      onUpdate(updated);
      setShowReject(false);
      setRejectFeedback("");
    } finally { setLoading(false); }
  }

  return (
    <div className={`rounded-xl bg-white p-5 shadow-sm ring-1 ${question.status === "approved" ? "ring-green-200" : question.status === "rejected" ? "ring-red-200" : "ring-gray-100"}`}>
      <div className="mb-3 flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-gray-900 leading-relaxed">{question.question_text}</p>
        <div className="flex gap-1 shrink-0">
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${COMPLEXITY_BADGE[question.complexity] || "bg-gray-100 text-gray-600"}`}>{question.complexity}</span>
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[question.status] || "bg-gray-100 text-gray-600"}`}>{question.status}</span>
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        {question.options.map(opt => (
          <div key={opt.id} className={`rounded-lg border px-3 py-2 text-sm ${question.correct_option_ids.includes(opt.id) ? "border-green-300 bg-green-50" : "border-gray-200"}`}>
            <span className="font-medium">{opt.label}.</span> {opt.text}
          </div>
        ))}
      </div>

      {question.explanation && (
        <div className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-700">
          <span className="font-medium">Explanation: </span>{question.explanation}
        </div>
      )}

      {question.topic && <p className="text-xs text-gray-400 mb-2">Topic: {question.topic}{question.subtopic ? ` › ${question.subtopic}` : ""}</p>}

      {question.status === "draft" && (
        <div className="flex gap-2 flex-wrap">
          <button onClick={accept} disabled={loading} className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50">Accept</button>
          <button onClick={() => setShowReject(!showReject)} className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50">Reject</button>
        </div>
      )}

      {showReject && (
        <div className="mt-3 space-y-2">
          <textarea
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            rows={2}
            placeholder="Explain why this question is rejected..."
            value={rejectFeedback}
            onChange={e => setRejectFeedback(e.target.value)}
          />
          <div className="flex gap-2">
            <button onClick={reject} disabled={loading || !rejectFeedback.trim()} className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50">Confirm Reject</button>
            <button onClick={() => setShowReject(false)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Review Batch View ─────────────────────────────────────────────────────────

function BatchReview({ batchId, onBack }: { batchId: string; onBack: () => void }) {
  const [batch, setBatch] = useState<MCQBatchWithQuestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenFeedback, setRegenFeedback] = useState("");
  const [regenJobId, setRegenJobId] = useState("");
  const [bulkFeedback, setBulkFeedback] = useState("");
  const [showBulkReject, setShowBulkReject] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const b = await mcqService.getBatch(batchId);
      setBatch(b);
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [batchId]);

  function handleQuestionUpdate(updated: MCQQuestion) {
    setBatch(prev => prev ? { ...prev, questions: prev.questions.map(q => q.id === updated.id ? updated : q) } : prev);
  }

  async function handleAcceptAll() {
    await mcqService.acceptAll(batchId);
    load();
  }

  async function handleRejectAll() {
    if (!bulkFeedback.trim()) return;
    await mcqService.rejectAll(batchId, bulkFeedback);
    setBulkFeedback(""); setShowBulkReject(false);
    load();
  }

  async function handleRegenerate() {
    if (!regenFeedback.trim()) return;
    const job = await mcqService.regenerateBatch(batchId, regenFeedback);
    setRegenJobId(job.id);
    setRegenFeedback("");
  }

  if (loading) return <div className="p-6 text-sm text-gray-500">Loading batch…</div>;
  if (!batch) return <div className="p-6 text-sm text-red-500">Batch not found.</div>;

  const rejected = batch.questions.filter(q => q.status === "rejected");
  const approved = batch.questions.filter(q => q.status === "approved");
  const draft = batch.questions.filter(q => q.status === "draft");

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <button onClick={onBack} className="text-sm text-brand-600 hover:underline">← Back to batches</button>
        <span className="text-xs text-gray-400">{batch.batch_type} · {batch.total_questions} questions</span>
        <div className="ml-auto flex gap-2 flex-wrap">
          <span className="text-xs text-green-600">{approved.length} accepted</span>
          <span className="text-xs text-red-600">{rejected.length} rejected</span>
          <span className="text-xs text-gray-500">{draft.length} pending</span>
        </div>
      </div>

      {draft.length > 0 && (
        <div className="mb-4 flex gap-2 flex-wrap">
          <button onClick={handleAcceptAll} className="rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700">Accept All Pending</button>
          <button onClick={() => setShowBulkReject(!showBulkReject)} className="rounded-lg border border-red-300 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50">Reject All Pending</button>
        </div>
      )}

      {showBulkReject && (
        <div className="mb-4 space-y-2">
          <textarea className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" rows={2} placeholder="Reason for rejecting all..." value={bulkFeedback} onChange={e => setBulkFeedback(e.target.value)} />
          <div className="flex gap-2">
            <button onClick={handleRejectAll} disabled={!bulkFeedback.trim()} className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50">Confirm Reject All</button>
            <button onClick={() => setShowBulkReject(false)} className="text-xs text-gray-500 hover:underline">Cancel</button>
          </div>
        </div>
      )}

      {regenJobId && (
        <div className="mb-4">
          <JobStatusPoller jobId={regenJobId} onComplete={() => { setRegenJobId(""); load(); }} />
        </div>
      )}

      {rejected.length > 0 && !regenJobId && (
        <div className="mb-4 rounded-xl bg-orange-50 p-4 space-y-2">
          <p className="text-sm font-medium text-orange-700">{rejected.length} rejected question{rejected.length > 1 ? "s" : ""} — regenerate them with feedback:</p>
          <textarea className="w-full rounded-lg border border-orange-200 px-3 py-2 text-sm" rows={2} placeholder="e.g. Questions are too easy, make them harder and more exam-focused" value={regenFeedback} onChange={e => setRegenFeedback(e.target.value)} />
          <button onClick={handleRegenerate} disabled={!regenFeedback.trim()} className="rounded-lg bg-orange-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-orange-700 disabled:opacity-50">Regenerate Rejected</button>
        </div>
      )}

      <div className="space-y-4">
        {batch.questions.map(q => (
          <QuestionCard key={q.id} question={q} batchId={batchId} onUpdate={handleQuestionUpdate} />
        ))}
      </div>
    </div>
  );
}

// ── Review Batches List Tab ───────────────────────────────────────────────────

function BatchesTab({ onOpenBatch }: { onOpenBatch: (id: string) => void }) {
  const [batches, setBatches] = useState<MCQReviewBatch[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    mcqService.listBatches().then(r => setBatches(r.items)).finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="text-sm text-gray-500">Loading…</div>;
  if (!batches.length) return <div className="text-sm text-gray-400">No review batches yet.</div>;

  const BATCH_STATUS: Record<string, string> = {
    pending: "bg-gray-100 text-gray-600",
    in_review: "bg-yellow-100 text-yellow-700",
    completed: "bg-green-100 text-green-700",
  };

  return (
    <div className="space-y-3">
      {batches.map(b => (
        <div key={b.id} className="flex items-center justify-between rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-medium text-gray-900 capitalize">{b.batch_type} batch</span>
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${BATCH_STATUS[b.status] || "bg-gray-100 text-gray-600"}`}>{b.status}</span>
            </div>
            <p className="text-xs text-gray-500">
              {b.total_questions} questions · {b.accepted_count} accepted · {b.rejected_count} rejected · {new Date(b.created_at).toLocaleDateString()}
            </p>
          </div>
          <button onClick={() => onOpenBatch(b.id)} className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-700">Review</button>
        </div>
      ))}
    </div>
  );
}

// ── Question Bank Tab ─────────────────────────────────────────────────────────

function QuestionBankTab() {
  const [questions, setQuestions] = useState<MCQQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ status: "approved", topic: "", complexity: "" });
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      const r = await mcqService.listQuestions({ ...filters, page, per_page: 20 });
      setQuestions(r.items);
      setTotal(r.total);
    } finally { setLoading(false); }
  }

  useEffect(() => { load(); }, [page, filters]);

  async function handleDelete(id: string) {
    if (!confirm("Delete this question?")) return;
    await mcqService.deleteQuestion(id);
    load();
  }

  async function toggleApproval(q: MCQQuestion) {
    if (q.status === "approved") {
      await mcqService.unapproveQuestion(q.id);
    } else {
      await mcqService.approveQuestion(q.id);
    }
    load();
  }

  return (
    <div>
      <div className="mb-4 flex gap-3 flex-wrap">
        <select className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm" value={filters.status} onChange={e => setFilters(p => ({ ...p, status: e.target.value }))}>
          <option value="">All Status</option>
          <option value="approved">Approved</option>
          <option value="draft">Draft</option>
          <option value="rejected">Rejected</option>
        </select>
        <select className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm" value={filters.complexity} onChange={e => setFilters(p => ({ ...p, complexity: e.target.value }))}>
          <option value="">All Difficulty</option>
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </select>
        <input className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm" placeholder="Filter by topic" value={filters.topic} onChange={e => setFilters(p => ({ ...p, topic: e.target.value }))} />
        <span className="ml-auto text-sm text-gray-500 self-center">{total} questions</span>
      </div>

      {loading ? (
        <div className="text-sm text-gray-500">Loading…</div>
      ) : questions.length === 0 ? (
        <div className="text-sm text-gray-400">No questions found.</div>
      ) : (
        <div className="space-y-3">
          {questions.map(q => (
            <div key={q.id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm text-gray-900 leading-relaxed flex-1">{q.question_text}</p>
                <div className="flex gap-1 shrink-0">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${COMPLEXITY_BADGE[q.complexity]}`}>{q.complexity}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[q.status]}`}>{q.status}</span>
                </div>
              </div>
              {q.topic && <p className="text-xs text-gray-400 mt-1">{q.topic}{q.subtopic ? ` › ${q.subtopic}` : ""}</p>}
              <div className="mt-3 flex gap-2">
                <button onClick={() => toggleApproval(q)} className={`rounded-lg px-3 py-1 text-xs font-medium ${q.status === "approved" ? "border border-gray-300 text-gray-600 hover:bg-gray-50" : "bg-green-600 text-white hover:bg-green-700"}`}>
                  {q.status === "approved" ? "Unapprove" : "Approve"}
                </button>
                <button onClick={() => handleDelete(q.id)} className="rounded-lg border border-red-200 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50">Delete</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {total > 20 && (
        <div className="mt-4 flex justify-center gap-2">
          <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs disabled:opacity-50">Previous</button>
          <span className="text-xs text-gray-500 self-center">Page {page}</span>
          <button onClick={() => setPage(p => p + 1)} disabled={page * 20 >= total} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs disabled:opacity-50">Next</button>
        </div>
      )}
    </div>
  );
}

// ── Manual Add Tab ────────────────────────────────────────────────────────────

const EMPTY_OPTIONS: MCQOption[] = [
  { id: "A", label: "A", text: "" },
  { id: "B", label: "B", text: "" },
  { id: "C", label: "C", text: "" },
  { id: "D", label: "D", text: "" },
];

function ManualAddTab({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState({
    question_text: "",
    explanation: "",
    chapter: "",
    topic: "",
    subtopic: "",
    complexity: "medium" as "easy" | "medium" | "hard",
    correct_option_id: "A",
  });
  const [options, setOptions] = useState<MCQOption[]>(EMPTY_OPTIONS.map(o => ({ ...o })));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.question_text.trim()) { setError("Question text is required."); return; }
    if (options.some(o => !o.text.trim())) { setError("All 4 options must have text."); return; }
    setLoading(true); setError(""); setSuccess(false);
    try {
      await mcqService.createQuestion({
        question_text: form.question_text,
        options,
        correct_option_ids: [form.correct_option_id],
        explanation: form.explanation || undefined,
        chapter: form.chapter || undefined,
        topic: form.topic || undefined,
        subtopic: form.subtopic || undefined,
        complexity: form.complexity,
      });
      setForm({ question_text: "", explanation: "", chapter: "", topic: "", subtopic: "", complexity: "medium", correct_option_id: "A" });
      setOptions(EMPTY_OPTIONS.map(o => ({ ...o })));
      setSuccess(true);
      onCreated();
    } catch (err: any) {
      setError(err?.response?.data?.detail?.message || "Failed to create question.");
    } finally { setLoading(false); }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-2xl space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Question Text *</label>
        <textarea rows={3} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.question_text} onChange={e => setForm(p => ({ ...p, question_text: e.target.value }))} />
      </div>
      <div className="space-y-2">
        <label className="block text-sm font-medium text-gray-700">Options *</label>
        {options.map((opt, i) => (
          <div key={opt.id} className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 cursor-pointer">
              <input type="radio" name="correct" value={opt.id} checked={form.correct_option_id === opt.id} onChange={() => setForm(p => ({ ...p, correct_option_id: opt.id }))} />
              <span className="text-sm font-medium text-gray-700 w-6">{opt.id}.</span>
            </label>
            <input className="flex-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" value={opt.text} onChange={e => {
              const updated = [...options]; updated[i] = { ...opt, text: e.target.value }; setOptions(updated);
            }} placeholder={`Option ${opt.id}`} />
          </div>
        ))}
        <p className="text-xs text-gray-500">Select the radio button next to the correct answer.</p>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Explanation</label>
        <textarea rows={2} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={form.explanation} onChange={e => setForm(p => ({ ...p, explanation: e.target.value }))} />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Difficulty</label>
          <select className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" value={form.complexity} onChange={e => setForm(p => ({ ...p, complexity: e.target.value as any }))}>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Topic</label>
          <input className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" value={form.topic} onChange={e => setForm(p => ({ ...p, topic: e.target.value }))} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Subtopic</label>
          <input className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" value={form.subtopic} onChange={e => setForm(p => ({ ...p, subtopic: e.target.value }))} />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Chapter</label>
          <input className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm" value={form.chapter} onChange={e => setForm(p => ({ ...p, chapter: e.target.value }))} />
        </div>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {success && <p className="text-sm text-green-600">Question created and approved.</p>}
      <button type="submit" disabled={loading} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {loading ? "Saving…" : "Add MCQ"}
      </button>
    </form>
  );
}

// ── Main MCQ Page ─────────────────────────────────────────────────────────────

export function AdminMCQ() {
  const [tab, setTab] = useState<Tab>("upload");
  const [activeJobId, setActiveJobId] = useState("");
  const [reviewBatchId, setReviewBatchId] = useState("");

  const TABS: { key: Tab; label: string }[] = [
    { key: "upload", label: "Upload Existing MCQs" },
    { key: "generate", label: "Generate from Content" },
    { key: "batches", label: "Review Batches" },
    { key: "bank", label: "Question Bank" },
    { key: "manual", label: "Manual Add" },
  ];

  function handleJobStart(jobId: string) {
    setActiveJobId(jobId);
  }

  function handleJobComplete(job: JobState) {
    setActiveJobId("");
    setTab("batches");
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-gray-900">MCQ System</h2>
        <p className="mt-1 text-sm text-gray-500">Upload, generate, review, and manage MCQ questions</p>
      </div>

      <div className="mb-6 flex gap-1 flex-wrap border-b border-gray-200">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => { setTab(t.key); setReviewBatchId(""); setActiveJobId(""); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t.key ? "border-brand-600 text-brand-700" : "border-transparent text-gray-500 hover:text-gray-700"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeJobId && (
        <div className="mb-6 max-w-lg">
          <p className="text-sm text-gray-600 mb-2">Processing in background…</p>
          <JobStatusPoller jobId={activeJobId} onComplete={handleJobComplete} onFail={() => setActiveJobId("")} />
        </div>
      )}

      {!activeJobId && (
        <>
          {tab === "upload" && <UploadTab onJobStart={handleJobStart} />}
          {tab === "generate" && <GenerateTab onJobStart={handleJobStart} />}
          {tab === "batches" && !reviewBatchId && <BatchesTab onOpenBatch={id => setReviewBatchId(id)} />}
          {tab === "batches" && reviewBatchId && <BatchReview batchId={reviewBatchId} onBack={() => setReviewBatchId("")} />}
          {tab === "bank" && <QuestionBankTab />}
          {tab === "manual" && <ManualAddTab onCreated={() => {}} />}
        </>
      )}
    </div>
  );
}
