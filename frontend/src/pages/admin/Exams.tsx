import { useRef, useState } from "react";
import { GraduationCap, Plus, Archive, RotateCcw, Trash2 } from "lucide-react";
import {
  PageHeader, Card, Button, FormField, TextInput, Textarea, Select,
  Badge, EmptyState, PageLoader, Modal, useToast,
} from "../../components/ui";
import { examsService, type ExamType } from "../../services/exams";
import { syllabusService } from "../../services/syllabus";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import { useExam } from "../../context/ExamContext";

export function AdminExams() {
  const { exams, isLoading, refresh, setSelectedExamId } = useExam();
  const toast = useToast();

  const [examType, setExamType] = useState<ExamType>("objective");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [syllabusFile, setSyllabusFile] = useState<File | null>(null);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const create = async () => {
    if (!name.trim()) {
      toast.error("Exam name is required.");
      return;
    }
    setCreating(true);
    setImportJobId(null);
    try {
      const exam = await examsService.create({ exam_type: examType, name: name.trim(), description: description.trim() || undefined });
      await refresh();
      setSelectedExamId(exam.id);

      // Optionally auto-fill the syllabus from an uploaded PDF/Word file. The exam is
      // already created; extraction runs in the background and the admin can edit it on
      // the Syllabus page afterward. A failed import just leaves the syllabus empty.
      if (syllabusFile) {
        try {
          const { job_id } = await syllabusService.importFromPdf(exam.id, syllabusFile);
          setImportJobId(job_id);
          toast.success("Exam created. Extracting syllabus from your file…");
        } catch {
          toast.error("Exam created, but the syllabus file could not be uploaded. Add the syllabus manually.");
        }
      } else {
        toast.success("Exam created.");
      }

      setName("");
      setDescription("");
      setSyllabusFile(null);
      if (fileRef.current) fileRef.current.value = "";
    } catch {
      toast.error("Could not create exam.");
    } finally {
      setCreating(false);
    }
  };

  const toggleArchive = async (id: string, status: string) => {
    try {
      await examsService.update(id, { status: status === "active" ? "archived" : "active" });
      await refresh();
    } catch {
      toast.error("Could not update exam.");
    }
  };

  // Deliberate, GitHub-style delete: the admin must TYPE an exact phrase (no paste) in a
  // modal before the destructive delete is enabled — guards against accidental clicks.
  const CONFIRM_PHRASE = "delete please";
  const [pendingDelete, setPendingDelete] = useState<{ id: string; name: string } | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);

  const askDelete = (exam: { id: string; name: string }) => {
    setConfirmText("");
    setPendingDelete(exam);
  };

  const closeDelete = () => {
    if (deleting) return;
    setPendingDelete(null);
    setConfirmText("");
  };

  const confirmDelete = async () => {
    if (!pendingDelete || confirmText !== CONFIRM_PHRASE) return;
    setDeleting(true);
    try {
      await examsService.remove(pendingDelete.id);
      toast.success("Exam deleted.");
      setPendingDelete(null);
      setConfirmText("");
      await refresh();
    } catch {
      toast.error("Could not delete exam.");
    } finally {
      setDeleting(false);
    }
  };

  const canDelete = confirmText === CONFIRM_PHRASE;

  if (isLoading) return <PageLoader />;

  return (
    <div>
      <PageHeader
        title="Exams"
        description="Each exam is strictly objective OR subjective. All content, tests, tutors and student enrollment operate within an exam."
        icon={<GraduationCap className="h-5 w-5" />}
      />

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        <Card className="p-5">
          <h2 className="mb-4 text-sm font-semibold text-gray-900">Create exam</h2>
          <div className="space-y-4">
            <FormField label="Exam type" required>
              <Select value={examType} onChange={(e) => setExamType(e.target.value as ExamType)}>
                <option value="objective">Objective</option>
                <option value="subjective">Subjective</option>
              </Select>
            </FormField>
            <FormField label="Name" required hint="e.g. RBB Assistant Level-4 (Objective)">
              <TextInput value={name} onChange={(e) => setName(e.target.value)} placeholder="Exam name" />
            </FormField>
            <FormField label="Description">
              <Textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Optional" />
            </FormField>
            <FormField
              label="Auto-fill syllabus from PDF"
              hint="Optional — upload the syllabus/course-outline (PDF or Word) and we'll extract its chapters, topics and subtopics automatically. You can still edit everything on the Syllabus page."
            >
              <input
                ref={fileRef}
                type="file"
                accept=".pdf,.docx,.doc"
                onChange={(e) => setSyllabusFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-gray-600 file:mr-3 file:rounded-lg file:border-0 file:bg-brand-50 file:px-3 file:py-2 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
              />
            </FormField>
            <Button onClick={create} loading={creating} icon={<Plus className="h-4 w-4" />} fullWidth>
              Create exam
            </Button>
            {importJobId && (
              <JobStatusPoller
                jobId={importJobId}
                onComplete={() => { toast.success("Syllabus imported — review it on the Syllabus page."); refresh(); }}
                onFail={() => toast.error("Syllabus extraction failed. You can add the syllabus manually.")}
              />
            )}
          </div>
        </Card>

        <Card className="p-5">
          <h2 className="mb-4 text-sm font-semibold text-gray-900">All exams</h2>
          {exams.length === 0 ? (
            <EmptyState title="No exams yet" description="Create your first exam to start adding content." />
          ) : (
            <div className="divide-y divide-gray-100">
              {exams.map((e) => (
                <div key={e.id} className="flex items-center justify-between gap-3 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium text-gray-900">{e.name}</span>
                      <Badge>{e.exam_type}</Badge>
                      {e.status === "archived" && <Badge>archived</Badge>}
                    </div>
                    {e.description && <p className="truncate text-xs text-gray-500">{e.description}</p>}
                  </div>
                  <div className="flex flex-shrink-0 items-center gap-1">
                    <Button
                      size="xs"
                      variant="ghost"
                      icon={e.status === "active" ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                      onClick={() => toggleArchive(e.id, e.status)}
                    >
                      {e.status === "active" ? "Archive" : "Restore"}
                    </Button>
                    <Button
                      size="xs"
                      variant="danger"
                      icon={<Trash2 className="h-4 w-4" />}
                      onClick={() => askDelete({ id: e.id, name: e.name })}
                    >
                      Delete
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Modal
        open={pendingDelete !== null}
        onClose={closeDelete}
        closeOnBackdrop={!deleting}
        size="md"
        title="Delete exam"
        subtitle="This action cannot be undone."
        footer={
          <>
            <Button variant="ghost" onClick={closeDelete} disabled={deleting}>Cancel</Button>
            <Button
              variant="danger"
              onClick={confirmDelete}
              loading={deleting}
              disabled={!canDelete || deleting}
              icon={<Trash2 className="h-4 w-4" />}
            >
              Delete this exam
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <p className="text-sm text-gray-700">
            You are about to permanently delete{" "}
            <span className="font-semibold text-gray-900">{pendingDelete?.name}</span> and{" "}
            <span className="font-semibold">ALL of its content</span> — syllabus, knowledge documents,
            MCQs, test sets &amp; attempts, subjective tests &amp; submissions, videos, tutor chats and
            student enrollments. Consider <span className="font-medium">archiving</span> instead if you
            only want to hide it.
          </p>
          <div>
            <p className="mb-1.5 text-sm text-gray-600">
              To confirm, type <span className="select-none font-semibold text-red-600">{CONFIRM_PHRASE}</span>{" "}
              below (typing required — pasting is disabled):
            </p>
            <input
              autoFocus
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              onPaste={(e) => e.preventDefault()}
              onDrop={(e) => e.preventDefault()}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              placeholder={CONFIRM_PHRASE}
              disabled={deleting}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-red-400 focus:ring-2 focus:ring-red-100 disabled:opacity-50"
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
