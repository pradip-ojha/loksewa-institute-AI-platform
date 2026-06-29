import { useState } from "react";
import { GraduationCap, Plus, Archive, RotateCcw } from "lucide-react";
import {
  PageHeader, Card, Button, FormField, TextInput, Textarea, Select,
  Badge, EmptyState, PageLoader, useToast,
} from "../../components/ui";
import { examsService, type ExamType } from "../../services/exams";
import { useExam } from "../../context/ExamContext";

export function AdminExams() {
  const { exams, isLoading, refresh, setSelectedExamId } = useExam();
  const toast = useToast();

  const [examType, setExamType] = useState<ExamType>("objective");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);

  const create = async () => {
    if (!name.trim()) {
      toast.error("Exam name is required.");
      return;
    }
    setCreating(true);
    try {
      const exam = await examsService.create({ exam_type: examType, name: name.trim(), description: description.trim() || undefined });
      toast.success("Exam created.");
      setName("");
      setDescription("");
      await refresh();
      setSelectedExamId(exam.id);
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
            <Button onClick={create} loading={creating} icon={<Plus className="h-4 w-4" />} fullWidth>
              Create exam
            </Button>
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
                  <Button
                    size="xs"
                    variant="ghost"
                    icon={e.status === "active" ? <Archive className="h-4 w-4" /> : <RotateCcw className="h-4 w-4" />}
                    onClick={() => toggleArchive(e.id, e.status)}
                  >
                    {e.status === "active" ? "Archive" : "Restore"}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
