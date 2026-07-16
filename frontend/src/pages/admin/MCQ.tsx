import { useEffect, useMemo, useState } from "react";
import { HelpCircle, ArrowLeft, Check } from "lucide-react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { mcqService } from "../../services/mcq";
import type { MCQQuestion, MCQReviewBatch, MCQBatchWithQuestions, MCQOption } from "../../services/mcq";
import { syllabusService } from "../../services/syllabus";
import type { ChapterNode } from "../../services/syllabus";
import { getErrorMessage } from "../../utils/error";
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
  Badge,
  StatusBadge,
  ConfirmDialog,
  EmptyState,
  Pagination,
  PageLoader,
  Modal,
} from "../../components/ui";

type Tab = "upload" | "generate" | "batches" | "bank" | "manual";

const COMPLEXITY_TONE: Record<string, "success" | "warning" | "danger" | "neutral"> = {
  easy: "success",
  medium: "warning",
  hard: "danger",
};

function ComplexityBadge({ value }: { value: string }) {
  return (
    <Badge tone={COMPLEXITY_TONE[value] ?? "neutral"} className="capitalize">
      {value}
    </Badge>
  );
}

/** Chapter › Topic › Subtopic line for a question. A missing chapter reads as a warning-tone
 * "Unassigned" chip so the admin can spot auto-detect questions that still need filing (§9.1). */
function QuestionTaxonomy({ question }: { question: MCQQuestion }) {
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
      {question.chapter ? (
        <Badge tone="neutral">{question.chapter}</Badge>
      ) : (
        <Badge tone="warning">Unassigned chapter</Badge>
      )}
      {question.topic && (
        <span className="text-gray-400">
          {question.topic}
          {question.subtopic ? ` › ${question.subtopic}` : ""}
        </span>
      )}
    </div>
  );
}

/** Full question editor (text, options, correct answer, explanation, chapter/topic/subtopic
 * dependent dropdowns, difficulty) wired to `updateQuestion`. Reused by the review batch and
 * the question bank so an admin can fix an auto-detected/unassigned chapter (§9.1, §9.3). */
function QuestionEditModal({
  question,
  chapters,
  onClose,
  onSaved,
}: {
  question: MCQQuestion;
  chapters: ChapterNode[];
  onClose: () => void;
  onSaved: (q: MCQQuestion) => void;
}) {
  const [form, setForm] = useState({
    question_text: question.question_text,
    explanation: question.explanation ?? "",
    chapter: question.chapter ?? "",
    topic: question.topic ?? "",
    subtopic: question.subtopic ?? "",
    complexity: (question.complexity as "easy" | "medium" | "hard") ?? "medium",
    correct_option_id: question.correct_option_ids[0] ?? "A",
  });
  const [options, setOptions] = useState<MCQOption[]>(question.options.map((o) => ({ ...o })));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const chapterTopics = useMemo(() => chapters.find((c) => c.chapter === form.chapter)?.topics ?? [], [form.chapter, chapters]);
  const topicSubtopics = useMemo(() => chapterTopics.find((t) => t.topic === form.topic)?.subtopics ?? [], [form.topic, chapterTopics]);

  // Changing chapter resets topic/subtopic (they belong to the old chapter); changing topic
  // resets subtopic. The backend applies the cleared values so no stale label survives.
  const handleChapterChange = (value: string) => setForm((p) => ({ ...p, chapter: value, topic: "", subtopic: "" }));
  const handleTopicChange = (value: string) => setForm((p) => ({ ...p, topic: value, subtopic: "" }));

  async function save() {
    if (!form.question_text.trim()) return setError("Question text is required.");
    if (options.some((o) => !o.text.trim())) return setError("All options must have text.");
    setLoading(true);
    setError("");
    try {
      const updated = await mcqService.updateQuestion(question.id, {
        question_text: form.question_text,
        options,
        correct_option_ids: [form.correct_option_id],
        explanation: form.explanation,
        chapter: form.chapter,
        topic: form.topic,
        subtopic: form.subtopic,
        complexity: form.complexity,
      });
      onSaved(updated);
      onClose();
    } catch (err: any) {
      setError(getErrorMessage(err, "Failed to save question."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Edit question"
      size="lg"
      closeOnBackdrop={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            Cancel
          </Button>
          <Button onClick={save} loading={loading}>
            Save changes
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FormField label="Question Text" required>
          <Textarea rows={3} value={form.question_text} onChange={(e) => setForm((p) => ({ ...p, question_text: e.target.value }))} />
        </FormField>
        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-700">
            Options <span className="text-danger-500">*</span>
          </label>
          {options.map((opt, i) => (
            <div key={opt.id} className="flex items-center gap-2">
              <label className="flex cursor-pointer items-center gap-1.5">
                <input
                  type="radio"
                  name="edit-correct"
                  value={opt.id}
                  checked={form.correct_option_id === opt.id}
                  onChange={() => setForm((p) => ({ ...p, correct_option_id: opt.id }))}
                />
                <span className="w-6 text-sm font-medium text-gray-700">{opt.id}.</span>
              </label>
              <TextInput
                value={opt.text}
                onChange={(e) => {
                  const updated = [...options];
                  updated[i] = { ...opt, text: e.target.value };
                  setOptions(updated);
                }}
                placeholder={`Option ${opt.id}`}
              />
            </div>
          ))}
          <p className="text-xs text-gray-500">Select the radio button next to the correct answer.</p>
        </div>
        <FormField label="Explanation">
          <Textarea rows={2} value={form.explanation} onChange={(e) => setForm((p) => ({ ...p, explanation: e.target.value }))} />
        </FormField>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <FormField label="Difficulty">
            <Select value={form.complexity} onChange={(e) => setForm((p) => ({ ...p, complexity: e.target.value as any }))}>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </Select>
          </FormField>
          <FormField label="Chapter">
            <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
              <option value="">— Unassigned —</option>
              {chapters.map((c) => (
                <option key={c.chapter} value={c.chapter}>
                  {c.chapter}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Topic">
            <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)} disabled={chapterTopics.length === 0}>
              <option value="">{chapterTopics.length > 0 ? "— None —" : form.chapter ? "No topics" : "Select chapter"}</option>
              {chapterTopics.map((t) => (
                <option key={t.topic} value={t.topic}>
                  {t.topic}
                </option>
              ))}
            </Select>
          </FormField>
          <FormField label="Subtopic">
            <Select value={form.subtopic} onChange={(e) => setForm((p) => ({ ...p, subtopic: e.target.value }))} disabled={topicSubtopics.length === 0}>
              <option value="">{topicSubtopics.length > 0 ? "— None —" : form.topic ? "No subtopics" : "Select topic"}</option>
              {topicSubtopics.map((s) => (
                <option key={s.id} value={s.subtopic}>
                  {s.subtopic}
                </option>
              ))}
            </Select>
          </FormField>
        </div>
        {error && <Alert>{error}</Alert>}
      </div>
    </Modal>
  );
}

// ── Upload Existing MCQ Tab ───────────────────────────────────────────────────

function UploadTab({ onJobStart, chapters }: { onJobStart: (jobId: string, batchMode: "extract") => void; chapters: ChapterNode[] }) {
  const { selectedExamId } = useExam();
  const [form, setForm] = useState({ display_name: "", chapter: "", topic: "", subtopic: "", custom_instruction: "" });
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const chapterTopics = useMemo(() => chapters.find((c) => c.chapter === form.chapter)?.topics ?? [], [form.chapter, chapters]);
  const availableSubtopics = useMemo(() => chapterTopics.find((t) => t.topic === form.topic)?.subtopics ?? [], [form.topic, chapterTopics]);

  const handleChapterChange = (value: string) => setForm((p) => ({ ...p, chapter: value, topic: "", subtopic: "" }));
  const handleTopicChange = (value: string) => setForm((p) => ({ ...p, topic: value, subtopic: "" }));

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return setError("Please select a file.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    if (!form.display_name.trim()) return setError("Document name is required.");
    // Chapter is OPTIONAL here: blank => the extractor auto-assigns a chapter per question
    // (for multi-chapter model papers). A chosen chapter locks every question to it.
    setLoading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("display_name", form.display_name);
      fd.append("exam_id", selectedExamId);
      if (form.chapter) fd.append("chapter", form.chapter);
      fd.append("topic", form.topic);
      fd.append("subtopic", form.subtopic);
      fd.append("custom_instruction", form.custom_instruction);
      const result = await mcqService.uploadDocument(fd);
      onJobStart(result.job_id, "extract");
    } catch (err: any) {
      setError(getErrorMessage(err, "Upload failed."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4">
      <FormField label="Document Display Name" required>
        <TextInput value={form.display_name} onChange={(e) => setForm((p) => ({ ...p, display_name: e.target.value }))} placeholder="e.g. Banking MCQ Set 2024" />
      </FormField>
      <FormField label="PDF or Word File" required>
        <input aria-label="PDF or Word File" type="file" accept=".pdf,.doc,.docx" className="w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      </FormField>
      <FormField label="Chapter (optional)">
        <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
          <option value="">— Auto-detect from document —</option>
          {chapters.map((c) => (
            <option key={c.chapter} value={c.chapter}>
              {c.chapter}
            </option>
          ))}
        </Select>
        <p className="mt-1 text-xs text-slate-500">
          Leave blank for a multi-chapter model paper — the AI assigns each question's chapter,
          then its topic. Pick a chapter to lock every question to it.
        </p>
      </FormField>
      <div className="grid grid-cols-2 gap-3">
        <FormField label="Topic (optional)">
          <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)} disabled={chapterTopics.length === 0}>
            <option value="">{chapterTopics.length > 0 ? "— Auto-detect —" : form.chapter ? "No topics" : "Auto-detect"}</option>
            {chapterTopics.map((t) => (
              <option key={t.topic} value={t.topic}>
                {t.topic}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Subtopic (optional)">
          <Select value={form.subtopic} onChange={(e) => setForm((p) => ({ ...p, subtopic: e.target.value }))} disabled={availableSubtopics.length === 0}>
            <option value="">{availableSubtopics.length > 0 ? "— Auto-detect —" : form.topic ? "No subtopics" : "Auto-detect"}</option>
            {availableSubtopics.map((s) => (
              <option key={s.id} value={s.subtopic}>
                {s.subtopic}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
      <FormField label="Custom Extraction Instruction (optional)">
        <Textarea rows={3} value={form.custom_instruction} onChange={(e) => setForm((p) => ({ ...p, custom_instruction: e.target.value }))} placeholder="e.g. Focus on questions about monetary policy" />
      </FormField>
      {error && <Alert>{error}</Alert>}
      <Button type="submit" loading={loading}>
        Extract MCQs
      </Button>
    </form>
  );
}

// ── Generate from Content Tab ─────────────────────────────────────────────────

function GenerateTab({ onJobStart, chapters }: { onJobStart: (jobId: string, mode: "generate") => void; chapters: ChapterNode[] }) {
  const { selectedExamId } = useExam();
  const [form, setForm] = useState({ display_name: "", chapter: "", topic: "", subtopic: "", custom_instruction: "", count: "10" });
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const chapterTopics = useMemo(() => chapters.find((c) => c.chapter === form.chapter)?.topics ?? [], [form.chapter, chapters]);
  const availableSubtopics = useMemo(() => chapterTopics.find((t) => t.topic === form.topic)?.subtopics ?? [], [form.topic, chapterTopics]);

  const handleChapterChange = (value: string) => setForm((p) => ({ ...p, chapter: value, topic: "", subtopic: "" }));
  const handleTopicChange = (value: string) => setForm((p) => ({ ...p, topic: value, subtopic: "" }));

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return setError("Please select a file.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    if (!form.display_name.trim()) return setError("Document name is required.");
    if (!form.chapter) return setError("Chapter is required.");
    setLoading(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("display_name", form.display_name);
      fd.append("exam_id", selectedExamId);
      fd.append("chapter", form.chapter);
      fd.append("count", form.count);
      fd.append("topic", form.topic);
      fd.append("subtopic", form.subtopic);
      fd.append("custom_instruction", form.custom_instruction);
      const result = await mcqService.generateFromContent(fd);
      onJobStart(result.job_id, "generate");
    } catch (err: any) {
      setError(getErrorMessage(err, "Generation failed."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4">
      <FormField label="Content Display Name" required>
        <TextInput value={form.display_name} onChange={(e) => setForm((p) => ({ ...p, display_name: e.target.value }))} placeholder="e.g. Banking Notes Chapter 3" />
      </FormField>
      <FormField label="Content File (PDF or Word)" required>
        <input aria-label="Content File" type="file" accept=".pdf,.doc,.docx" className="w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      </FormField>
      <FormField label="Number of MCQs to Generate">
        <TextInput type="number" min={1} max={100} className="w-32" value={form.count} onChange={(e) => setForm((p) => ({ ...p, count: e.target.value }))} />
      </FormField>
      <FormField label="Chapter" required>
        <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
          <option value="">— Select chapter —</option>
          {chapters.map((c) => (
            <option key={c.chapter} value={c.chapter}>
              {c.chapter}
            </option>
          ))}
        </Select>
      </FormField>
      <div className="grid grid-cols-2 gap-3">
        <FormField label="Topic (optional)">
          <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)} disabled={chapterTopics.length === 0}>
            <option value="">{chapterTopics.length > 0 ? "— Auto-detect —" : form.chapter ? "No topics" : "Select chapter first"}</option>
            {chapterTopics.map((t) => (
              <option key={t.topic} value={t.topic}>
                {t.topic}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Subtopic (optional)">
          <Select value={form.subtopic} onChange={(e) => setForm((p) => ({ ...p, subtopic: e.target.value }))} disabled={availableSubtopics.length === 0}>
            <option value="">{availableSubtopics.length > 0 ? "— Auto-detect —" : form.topic ? "No subtopics" : "Select topic first"}</option>
            {availableSubtopics.map((s) => (
              <option key={s.id} value={s.subtopic}>
                {s.subtopic}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
      <FormField label="Custom Instruction (optional)">
        <Textarea rows={3} value={form.custom_instruction} onChange={(e) => setForm((p) => ({ ...p, custom_instruction: e.target.value }))} />
      </FormField>
      {error && <Alert>{error}</Alert>}
      <Button type="submit" loading={loading}>
        Generate MCQs
      </Button>
    </form>
  );
}

// ── Question Card ─────────────────────────────────────────────────────────────

function QuestionCard({ question, batchId, chapters, onUpdate }: { question: MCQQuestion; batchId: string; chapters: ChapterNode[]; onUpdate: (q: MCQQuestion) => void }) {
  const [rejectFeedback, setRejectFeedback] = useState("");
  const [showReject, setShowReject] = useState(false);
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(false);

  async function accept() {
    setLoading(true);
    try {
      onUpdate(await mcqService.acceptQuestion(batchId, question.id));
    } finally {
      setLoading(false);
    }
  }

  async function reject() {
    if (!rejectFeedback.trim()) return;
    setLoading(true);
    try {
      onUpdate(await mcqService.rejectQuestion(batchId, question.id, rejectFeedback));
      setShowReject(false);
      setRejectFeedback("");
    } finally {
      setLoading(false);
    }
  }

  const borderTone =
    question.status === "approved" ? "border-success-200" : question.status === "rejected" ? "border-danger-200" : "border-gray-200";

  return (
    <div className={`rounded-lg border bg-white p-5 ${borderTone}`}>
      <div className="mb-3 flex items-start justify-between gap-2">
        {question.question_text ? (
          <p className="flex-1 text-sm font-medium leading-relaxed text-gray-900">{question.question_text}</p>
        ) : (
          <p className="flex-1 text-sm italic text-danger-500">[Question text missing — regenerate this batch]</p>
        )}
        <div className="flex shrink-0 gap-1">
          <ComplexityBadge value={question.complexity} />
          <StatusBadge status={question.status} />
        </div>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-2">
        {question.options.map((opt) => (
          <div
            key={opt.id}
            className={`rounded-md border px-3 py-2 text-sm ${question.correct_option_ids.includes(opt.id) ? "border-success-300 bg-success-50" : "border-gray-200"}`}
          >
            <span className="font-medium">{opt.label}.</span> {opt.text}
          </div>
        ))}
      </div>

      {question.explanation && (
        <div className="mb-3 rounded-md bg-info-50 px-3 py-2 text-xs text-info-700">
          <span className="font-medium">Explanation: </span>
          {question.explanation}
        </div>
      )}

      <div className="mb-2">
        <QuestionTaxonomy question={question} />
      </div>

      <div className="flex flex-wrap gap-2">
        {question.status === "draft" && (
          <>
            <Button size="xs" onClick={accept} loading={loading} icon={<Check className="h-3.5 w-3.5" />}>
              Accept
            </Button>
            <Button size="xs" variant="secondary" className="text-danger-600" onClick={() => setShowReject(!showReject)}>
              Reject
            </Button>
          </>
        )}
        <Button size="xs" variant="ghost" onClick={() => setEditing(true)}>
          Edit
        </Button>
      </div>

      {editing && (
        <QuestionEditModal
          question={question}
          chapters={chapters}
          onClose={() => setEditing(false)}
          onSaved={onUpdate}
        />
      )}

      {showReject && (
        <div className="mt-3 space-y-2">
          <Textarea rows={2} placeholder="Explain why this question is rejected..." value={rejectFeedback} onChange={(e) => setRejectFeedback(e.target.value)} />
          <div className="flex gap-2">
            <Button size="xs" variant="danger" onClick={reject} disabled={loading || !rejectFeedback.trim()}>
              Confirm Reject
            </Button>
            <Button size="xs" variant="ghost" onClick={() => setShowReject(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Review Batch View ─────────────────────────────────────────────────────────

function BatchReview({ batchId, chapters, onBack }: { batchId: string; chapters: ChapterNode[]; onBack: () => void }) {
  const [batch, setBatch] = useState<MCQBatchWithQuestions | null>(null);
  const [loading, setLoading] = useState(true);
  const [regenFeedback, setRegenFeedback] = useState("");
  const [regenJobId, setRegenJobId] = useState("");
  const [bulkFeedback, setBulkFeedback] = useState("");
  const [showBulkReject, setShowBulkReject] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setBatch(await mcqService.getBatch(batchId));
    } catch {
      setBatch(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  function handleQuestionUpdate(updated: MCQQuestion) {
    setBatch((prev) => (prev ? { ...prev, questions: prev.questions.map((q) => (q.id === updated.id ? updated : q)) } : prev));
  }

  async function handleAcceptAll() {
    await mcqService.acceptAll(batchId);
    load();
  }

  async function handleRejectAll() {
    if (!bulkFeedback.trim()) return;
    await mcqService.rejectAll(batchId, bulkFeedback);
    setBulkFeedback("");
    setShowBulkReject(false);
    load();
  }

  async function handleRegenerate() {
    if (!regenFeedback.trim()) return;
    const job = await mcqService.regenerateBatch(batchId, regenFeedback);
    setRegenJobId(job.id);
    setRegenFeedback("");
  }

  if (loading) return <PageLoader label="Loading batch…" />;
  if (!batch) return <Alert className="m-2">Batch not found.</Alert>;

  const rejected = batch.questions.filter((q) => q.status === "rejected");
  const approved = batch.questions.filter((q) => q.status === "approved");
  const draft = batch.questions.filter((q) => q.status === "draft");

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Button variant="ghost" size="sm" icon={<ArrowLeft className="h-4 w-4" />} onClick={onBack}>
          Back to batches
        </Button>
        <span className="text-xs text-gray-400">
          {batch.batch_type} · {batch.total_questions} questions
        </span>
        <div className="ml-auto flex flex-wrap gap-2 tabular-nums">
          <span className="text-xs text-success-600">{approved.length} accepted</span>
          <span className="text-xs text-danger-600">{rejected.length} rejected</span>
          <span className="text-xs text-gray-500">{draft.length} pending</span>
        </div>
      </div>

      {draft.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          <Button size="sm" onClick={handleAcceptAll}>
            Accept All Pending
          </Button>
          <Button size="sm" variant="secondary" className="text-danger-600" onClick={() => setShowBulkReject(!showBulkReject)}>
            Reject All Pending
          </Button>
        </div>
      )}

      {showBulkReject && (
        <div className="mb-4 space-y-2">
          <Textarea rows={2} placeholder="Reason for rejecting all..." value={bulkFeedback} onChange={(e) => setBulkFeedback(e.target.value)} />
          <div className="flex gap-2">
            <Button size="sm" variant="danger" onClick={handleRejectAll} disabled={!bulkFeedback.trim()}>
              Confirm Reject All
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setShowBulkReject(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {regenJobId && (
        <div className="mb-4">
          <JobStatusPoller
            jobId={regenJobId}
            onComplete={() => {
              setRegenJobId("");
              load();
            }}
          />
        </div>
      )}

      {rejected.length > 0 && !regenJobId && (
        <div className="mb-4 space-y-2 rounded-lg border border-warning-100 bg-warning-50 p-4">
          <p className="text-sm font-medium text-warning-700">
            {rejected.length} rejected question{rejected.length > 1 ? "s" : ""} — regenerate them with feedback:
          </p>
          <Textarea rows={2} placeholder="e.g. Questions are too easy, make them harder and more exam-focused" value={regenFeedback} onChange={(e) => setRegenFeedback(e.target.value)} />
          <Button size="sm" variant="secondary" onClick={handleRegenerate} disabled={!regenFeedback.trim()}>
            Regenerate Rejected
          </Button>
        </div>
      )}

      <div className="space-y-4">
        {batch.questions.map((q) => (
          <QuestionCard key={q.id} question={q} batchId={batchId} chapters={chapters} onUpdate={handleQuestionUpdate} />
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
    mcqService
      .listBatches()
      .then((r) => setBatches(r.items))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <PageLoader />;
  if (!batches.length) return <EmptyState icon={<HelpCircle className="h-5 w-5" />} title="No review batches yet" />;

  return (
    <div className="space-y-3">
      {batches.map((b) => (
        <div key={b.id} className="flex items-center justify-between rounded-lg border border-gray-200 bg-white p-4">
          <div>
            <div className="mb-1 flex items-center gap-2">
              <span className="text-sm font-medium capitalize text-gray-900">{b.batch_type} batch</span>
              <StatusBadge status={b.status} />
            </div>
            <p className="text-xs tabular-nums text-gray-500">
              {b.total_questions} questions · {b.accepted_count} accepted · {b.rejected_count} rejected · {new Date(b.created_at).toLocaleDateString()}
            </p>
          </div>
          <Button size="sm" onClick={() => onOpenBatch(b.id)}>
            Review
          </Button>
        </div>
      ))}
    </div>
  );
}

// ── Question Bank Tab ─────────────────────────────────────────────────────────

function QuestionBankTab({ chapters }: { chapters: ChapterNode[] }) {
  const [questions, setQuestions] = useState<MCQQuestion[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({ status: "approved", topic: "", complexity: "", chapter: "" });
  const [loading, setLoading] = useState(true);
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [editing, setEditing] = useState<MCQQuestion | null>(null);

  const allTopics = useMemo(() => chapters.flatMap((c) => c.topics), [chapters]);

  async function load() {
    setLoading(true);
    try {
      const r = await mcqService.listQuestions({ ...filters, page, per_page: 20 });
      setQuestions(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filters]);

  async function doDelete() {
    if (!confirmDel) return;
    await mcqService.deleteQuestion(confirmDel);
    setConfirmDel(null);
    load();
  }

  async function toggleApproval(q: MCQQuestion) {
    if (q.status === "approved") await mcqService.unapproveQuestion(q.id);
    else await mcqService.approveQuestion(q.id);
    load();
  }

  function applyEdited(updated: MCQQuestion) {
    setQuestions((prev) => prev.map((q) => (q.id === updated.id ? updated : q)));
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select aria-label="Filter by Status" className="w-40" value={filters.status} onChange={(e) => setFilters((p) => ({ ...p, status: e.target.value }))}>
          <option value="">All Status</option>
          <option value="approved">Approved</option>
          <option value="draft">Draft</option>
          <option value="rejected">Rejected</option>
        </Select>
        <Select aria-label="Filter by Chapter" className="w-56" value={filters.chapter} onChange={(e) => { setPage(1); setFilters((p) => ({ ...p, chapter: e.target.value })); }}>
          <option value="">All Chapters</option>
          <option value={mcqService.UNASSIGNED_CHAPTER}>Unassigned chapter</option>
          {chapters.map((c) => (
            <option key={c.chapter} value={c.chapter}>
              {c.chapter}
            </option>
          ))}
        </Select>
        <Select aria-label="Filter by Difficulty" className="w-40" value={filters.complexity} onChange={(e) => setFilters((p) => ({ ...p, complexity: e.target.value }))}>
          <option value="">All Difficulty</option>
          <option value="easy">Easy</option>
          <option value="medium">Medium</option>
          <option value="hard">Hard</option>
        </Select>
        <Select aria-label="Filter by Topic" className="w-56" value={filters.topic} onChange={(e) => { setPage(1); setFilters((p) => ({ ...p, topic: e.target.value })); }}>
          <option value="">All Topics</option>
          {allTopics.map((t) => (
            <option key={t.topic} value={t.topic}>
              {t.topic}
            </option>
          ))}
        </Select>
        <span className="ml-auto self-center text-sm tabular-nums text-gray-500">{total} questions</span>
      </div>

      {loading ? (
        <PageLoader />
      ) : questions.length === 0 ? (
        <EmptyState icon={<HelpCircle className="h-5 w-5" />} title="No questions found" />
      ) : (
        <div className="space-y-3">
          {questions.map((q) => (
            <div key={q.id} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="flex items-start justify-between gap-2">
                <p className="flex-1 text-sm leading-relaxed text-gray-900">{q.question_text}</p>
                <div className="flex shrink-0 gap-1">
                  <ComplexityBadge value={q.complexity} />
                  <StatusBadge status={q.status} />
                </div>
              </div>
              <QuestionTaxonomy question={q} />
              <div className="mt-3 flex gap-2">
                <Button size="xs" variant={q.status === "approved" ? "secondary" : "primary"} onClick={() => toggleApproval(q)}>
                  {q.status === "approved" ? "Unapprove" : "Approve"}
                </Button>
                <Button size="xs" variant="ghost" onClick={() => setEditing(q)}>
                  Edit
                </Button>
                <Button size="xs" variant="secondary" className="text-danger-600" onClick={() => setConfirmDel(q.id)}>
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {total > 20 && <Pagination className="mt-4" page={page} pageSize={20} total={total} onPage={setPage} />}

      {editing && (
        <QuestionEditModal
          question={editing}
          chapters={chapters}
          onClose={() => setEditing(null)}
          onSaved={applyEdited}
        />
      )}

      <ConfirmDialog
        open={!!confirmDel}
        title="Delete question"
        description="Delete this question? This cannot be undone."
        confirmLabel="Delete"
        tone="danger"
        onConfirm={doDelete}
        onClose={() => setConfirmDel(null)}
      />
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

function ManualAddTab({ onCreated, chapters }: { onCreated: () => void; chapters: ChapterNode[] }) {
  const { selectedExamId } = useExam();
  const [form, setForm] = useState({
    question_text: "",
    explanation: "",
    chapter: "",
    topic: "",
    subtopic: "",
    complexity: "medium" as "easy" | "medium" | "hard",
    correct_option_id: "A",
  });
  const [options, setOptions] = useState<MCQOption[]>(EMPTY_OPTIONS.map((o) => ({ ...o })));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const chapterTopics = useMemo(() => chapters.find((c) => c.chapter === form.chapter)?.topics ?? [], [form.chapter, chapters]);
  const topicSubtopics = useMemo(() => chapterTopics.find((t) => t.topic === form.topic)?.subtopics ?? [], [form.topic, chapterTopics]);

  const handleChapterChange = (value: string) => setForm((p) => ({ ...p, chapter: value, topic: "", subtopic: "" }));
  const handleTopicChange = (value: string) => setForm((p) => ({ ...p, topic: value, subtopic: "" }));

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.question_text.trim()) return setError("Question text is required.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    if (!form.chapter) return setError("Chapter is required.");
    if (options.some((o) => !o.text.trim())) return setError("All 4 options must have text.");
    setLoading(true);
    setError("");
    setSuccess(false);
    try {
      await mcqService.createQuestion({
        exam_id: selectedExamId,
        question_text: form.question_text,
        options,
        correct_option_ids: [form.correct_option_id],
        explanation: form.explanation || undefined,
        chapter: form.chapter,
        topic: form.topic || undefined,
        subtopic: form.subtopic || undefined,
        complexity: form.complexity,
      });
      setForm({ question_text: "", explanation: "", chapter: "", topic: "", subtopic: "", complexity: "medium", correct_option_id: "A" });
      setOptions(EMPTY_OPTIONS.map((o) => ({ ...o })));
      setSuccess(true);
      onCreated();
    } catch (err: any) {
      setError(getErrorMessage(err, "Failed to create question."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-2xl space-y-4">
      <FormField label="Question Text" required>
        <Textarea rows={3} value={form.question_text} onChange={(e) => setForm((p) => ({ ...p, question_text: e.target.value }))} />
      </FormField>
      <div className="space-y-2">
        <label className="block text-sm font-medium text-gray-700">
          Options <span className="text-danger-500">*</span>
        </label>
        {options.map((opt, i) => (
          <div key={opt.id} className="flex items-center gap-2">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" name="correct" value={opt.id} checked={form.correct_option_id === opt.id} onChange={() => setForm((p) => ({ ...p, correct_option_id: opt.id }))} />
              <span className="w-6 text-sm font-medium text-gray-700">{opt.id}.</span>
            </label>
            <TextInput
              value={opt.text}
              onChange={(e) => {
                const updated = [...options];
                updated[i] = { ...opt, text: e.target.value };
                setOptions(updated);
              }}
              placeholder={`Option ${opt.id}`}
            />
          </div>
        ))}
        <p className="text-xs text-gray-500">Select the radio button next to the correct answer.</p>
      </div>
      <FormField label="Explanation">
        <Textarea rows={2} value={form.explanation} onChange={(e) => setForm((p) => ({ ...p, explanation: e.target.value }))} />
      </FormField>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <FormField label="Difficulty">
          <Select value={form.complexity} onChange={(e) => setForm((p) => ({ ...p, complexity: e.target.value as any }))}>
            <option value="easy">Easy</option>
            <option value="medium">Medium</option>
            <option value="hard">Hard</option>
          </Select>
        </FormField>
        <FormField label="Chapter" required>
          <Select value={form.chapter} onChange={(e) => handleChapterChange(e.target.value)}>
            <option value="">— None —</option>
            {chapters.map((c) => (
              <option key={c.chapter} value={c.chapter}>
                {c.chapter}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Topic">
          <Select value={form.topic} onChange={(e) => handleTopicChange(e.target.value)} disabled={chapterTopics.length === 0}>
            <option value="">{chapterTopics.length > 0 ? "— None —" : form.chapter ? "No topics" : "Select chapter first"}</option>
            {chapterTopics.map((t) => (
              <option key={t.topic} value={t.topic}>
                {t.topic}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Subtopic">
          <Select value={form.subtopic} onChange={(e) => setForm((p) => ({ ...p, subtopic: e.target.value }))} disabled={topicSubtopics.length === 0}>
            <option value="">{topicSubtopics.length > 0 ? "— None —" : form.topic ? "No subtopics" : "Select topic first"}</option>
            {topicSubtopics.map((s) => (
              <option key={s.id} value={s.subtopic}>
                {s.subtopic}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
      {error && <Alert>{error}</Alert>}
      {success && <Alert tone="success">Question created and approved.</Alert>}
      <Button type="submit" loading={loading}>
        Add MCQ
      </Button>
    </form>
  );
}

// ── Main MCQ Page ─────────────────────────────────────────────────────────────

export function AdminMCQ() {
  const [tab, setTab] = useState<Tab>("upload");
  const [activeJobId, setActiveJobId] = useState("");
  const [reviewBatchId, setReviewBatchId] = useState("");
  const { selectedExamId } = useExam();
  const [syllabusChapters, setSyllabusChapters] = useState<ChapterNode[]>([]);

  useEffect(() => {
    if (!selectedExamId) {
      setSyllabusChapters([]);
      return;
    }
    syllabusService
      .get(selectedExamId)
      .then((tree) => setSyllabusChapters(tree.chapters))
      .catch(() => {});
  }, [selectedExamId]);

  const TABS = [
    { id: "upload", label: "Upload Existing MCQs" },
    { id: "generate", label: "Generate from Content" },
    { id: "batches", label: "Review Batches" },
    { id: "bank", label: "Question Bank" },
    { id: "manual", label: "Manual Add" },
  ];

  function handleJobStart(jobId: string) {
    setActiveJobId(jobId);
  }

  function handleJobComplete(_job: JobState) {
    setActiveJobId("");
    setTab("batches");
  }

  return (
    <div>
      <PageHeader title="MCQ System" description="Upload, generate, review, and manage MCQ questions." icon={<HelpCircle className="h-5 w-5" />} />

      <Tabs
        items={TABS}
        value={tab}
        onChange={(id) => {
          setTab(id as Tab);
          setReviewBatchId("");
          setActiveJobId("");
        }}
        className="mb-6"
      />

      {activeJobId && (
        <div className="mb-6 max-w-lg">
          <p className="mb-2 text-sm text-gray-600">Processing in background…</p>
          <JobStatusPoller jobId={activeJobId} onComplete={handleJobComplete} onFail={() => setActiveJobId("")} />
        </div>
      )}

      {!activeJobId && (
        <>
          {tab === "upload" && <UploadTab onJobStart={handleJobStart} chapters={syllabusChapters} />}
          {tab === "generate" && <GenerateTab onJobStart={handleJobStart} chapters={syllabusChapters} />}
          {tab === "batches" && !reviewBatchId && <BatchesTab onOpenBatch={(id) => setReviewBatchId(id)} />}
          {tab === "batches" && reviewBatchId && <BatchReview batchId={reviewBatchId} chapters={syllabusChapters} onBack={() => setReviewBatchId("")} />}
          {tab === "bank" && <QuestionBankTab chapters={syllabusChapters} />}
          {tab === "manual" && <ManualAddTab onCreated={() => {}} chapters={syllabusChapters} />}
        </>
      )}
    </div>
  );
}
