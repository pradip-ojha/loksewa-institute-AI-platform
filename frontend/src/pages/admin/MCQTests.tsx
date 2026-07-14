import { useCallback, useEffect, useState } from "react";
import { FileText, X, Check } from "lucide-react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { mcqTestsService } from "../../services/mcqTests";
import type { Blueprint, TestSet, TestSetPreview, TopicDistEntry } from "../../services/mcqTests";
import { syllabusService } from "../../services/syllabus";
import type { ChapterNode } from "../../services/syllabus";
import { useExam } from "../../context/ExamContext";
import { getErrorMessage } from "../../utils/error";
import { MCQAnalyticsView } from "./Analytics";
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
  type Column,
} from "../../components/ui";

type Tab = "create" | "sets" | "active" | "attempts" | "analytics";

// ── Create Blueprint Tab ──────────────────────────────────────────────────────

function CreateBlueprintTab({ chapters, onGenerated }: { chapters: ChapterNode[]; onGenerated: () => void }) {
  const { selectedExamId } = useExam();

  const [testName, setTestName] = useState("");
  const [totalTime, setTotalTime] = useState("60");
  const [numSets, setNumSets] = useState("1");
  const [customInstruction, setCustomInstruction] = useState("");
  const [rows, setRows] = useState<TopicDistEntry[]>([{ chapter: "", topic: "", subtopic: "", count: 5 }]);
  const [useDifficulty, setUseDifficulty] = useState(false);
  const [difficulty, setDifficulty] = useState({ easy: 0, medium: 0, hard: 0 });

  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const perSetTotal = rows.reduce((sum, r) => sum + (Number(r.count) || 0), 0);
  const diffTotal = difficulty.easy + difficulty.medium + difficulty.hard;

  function updateRow(i: number, patch: Partial<TopicDistEntry>) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  }
  function addRow() {
    setRows((prev) => [...prev, { chapter: "", topic: "", subtopic: "", count: 5 }]);
  }
  function removeRow(i: number) {
    setRows((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!testName.trim()) return setError("Test name is required.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    if (rows.length === 0 || perSetTotal === 0) return setError("Add at least one chapter row with a question count.");
    if (rows.some((r) => !r.chapter)) return setError("Every distribution row needs a chapter.");
    if (useDifficulty && diffTotal > perSetTotal) return setError("Difficulty totals cannot exceed total questions per set.");

    setLoading(true);
    try {
      const job = await mcqTestsService.createBlueprint({
        exam_id: selectedExamId,
        test_name: testName.trim(),
        total_time_minutes: Number(totalTime),
        num_sets: Number(numSets),
        topic_distribution: rows.map((r) => ({
          chapter: r.chapter,
          topic: r.topic || null,
          subtopic: r.subtopic || null,
          count: Number(r.count),
        })),
        difficulty_distribution: useDifficulty ? difficulty : null,
        custom_instruction: customInstruction || null,
      });
      setJobId(job.id);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to create blueprint."));
    } finally {
      setLoading(false);
    }
  }

  function handleJobComplete(_job: JobState) {
    onGenerated();
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-2xl space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <FormField label="Test Name" required className="sm:col-span-3">
          <TextInput value={testName} onChange={(e) => setTestName(e.target.value)} placeholder="e.g. Banking Practice Test 1" />
        </FormField>
        <FormField label="Total Time (min)" required>
          <TextInput type="number" min={1} value={totalTime} onChange={(e) => setTotalTime(e.target.value)} />
        </FormField>
        <FormField label="Number of Sets" required>
          <TextInput type="number" min={1} value={numSets} onChange={(e) => setNumSets(e.target.value)} />
        </FormField>
        <div className="flex items-end pb-2 text-sm text-gray-500">{perSetTotal} questions / set</div>
      </div>

      {/* Chapter (primary) / topic / subtopic distribution */}
      <div>
        <label className="mb-2 block text-sm font-medium text-gray-700">
          Chapter Distribution <span className="text-danger-500">*</span>{" "}
          <span className="font-normal text-gray-400">(topic/subtopic optional within a chapter)</span>
        </label>
        <div className="space-y-2">
          {rows.map((row, i) => {
            const chapterTopics = chapters.find((c) => c.chapter === row.chapter)?.topics ?? [];
            const subtopics = chapterTopics.find((t) => t.topic === row.topic)?.subtopics ?? [];
            return (
              <div key={i} className="grid grid-cols-[1fr_1fr_1fr_80px_auto] gap-2">
                <Select aria-label="Chapter" value={row.chapter ?? ""} onChange={(e) => updateRow(i, { chapter: e.target.value, topic: "", subtopic: "" })}>
                  <option value="">Select chapter</option>
                  {chapters.map((c) => (
                    <option key={c.chapter} value={c.chapter}>
                      {c.chapter}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Topic" value={row.topic ?? ""} onChange={(e) => updateRow(i, { topic: e.target.value, subtopic: "" })} disabled={!row.chapter}>
                  <option value="">Any topic</option>
                  {chapterTopics.map((t) => (
                    <option key={t.topic} value={t.topic}>
                      {t.topic}
                    </option>
                  ))}
                </Select>
                <Select aria-label="Subtopic" value={row.subtopic ?? ""} onChange={(e) => updateRow(i, { subtopic: e.target.value })} disabled={subtopics.length === 0}>
                  <option value="">Any subtopic</option>
                  {subtopics.map((s) => (
                    <option key={s.id} value={s.subtopic}>
                      {s.subtopic}
                    </option>
                  ))}
                </Select>
                <TextInput type="number" min={1} aria-label="Count" value={row.count} onChange={(e) => updateRow(i, { count: Number(e.target.value) })} />
                <Button type="button" variant="ghost" size="sm" onClick={() => removeRow(i)} disabled={rows.length === 1} className="text-danger-600 hover:bg-danger-50" icon={<X className="h-4 w-4" />} aria-label="Remove row" />
              </div>
            );
          })}
        </div>
        <button type="button" onClick={addRow} className="mt-2 text-sm font-medium text-brand-600 hover:text-brand-700">
          + Add chapter row
        </button>
      </div>

      {/* Difficulty distribution */}
      <div className="rounded-lg border border-gray-200 p-3">
        <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
          <input type="checkbox" checked={useDifficulty} onChange={(e) => setUseDifficulty(e.target.checked)} />
          Difficulty distribution (optional, applied within total)
        </label>
        {useDifficulty && (
          <div className="mt-3 grid grid-cols-3 gap-3">
            {(["easy", "medium", "hard"] as const).map((d) => (
              <FormField key={d} label={<span className="capitalize">{d}</span>}>
                <TextInput type="number" min={0} value={difficulty[d]} onChange={(e) => setDifficulty((p) => ({ ...p, [d]: Number(e.target.value) }))} />
              </FormField>
            ))}
            <p className={`col-span-3 text-xs ${diffTotal > perSetTotal ? "text-danger-600" : "text-gray-400"}`}>
              {diffTotal} of {perSetTotal} questions assigned a difficulty{diffTotal > perSetTotal ? " — exceeds total!" : ""}
            </p>
          </div>
        )}
      </div>

      <FormField label="Custom Instruction (optional)">
        <Textarea rows={2} value={customInstruction} onChange={(e) => setCustomInstruction(e.target.value)} />
      </FormField>

      {error && <Alert>{error}</Alert>}
      <Button type="submit" loading={loading}>
        Generate Test Sets
      </Button>

      {jobId && <JobStatusPoller jobId={jobId} className="mt-4 max-w-md" onComplete={handleJobComplete} onFail={handleJobComplete} />}
    </form>
  );
}

// ── Blueprint list (shows shortage breakdown) ─────────────────────────────────

function BlueprintList({ blueprints, onRegenerate }: { blueprints: Blueprint[]; onRegenerate: (id: string) => void }) {
  if (blueprints.length === 0) return <p className="text-sm text-gray-500">No blueprints yet.</p>;
  return (
    <div className="space-y-3">
      {blueprints.map((bp) => (
        <div key={bp.id} className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="font-medium text-gray-900">{bp.test_name}</p>
              <p className="text-xs text-gray-500">
                {bp.num_sets} set(s) · {bp.total_time_minutes} min · created {new Date(bp.created_at).toLocaleString()}
              </p>
            </div>
            <StatusBadge status={bp.status} />
          </div>

          {bp.status === "shortage" && bp.generation_result?.shortages?.length ? (
            <div className="mt-3 rounded-md border border-danger-100 bg-danger-50 p-3">
              <p className="text-sm font-medium text-danger-700">Not enough approved questions — no sets were created.</p>
              <div className="overflow-x-auto">
                <table className="mt-2 w-full text-xs text-danger-700">
                  <thead>
                    <tr className="text-left text-danger-600">
                      <th className="py-1 pr-2">Chapter</th>
                      <th className="pr-2">Topic</th>
                      <th className="pr-2">Subtopic</th>
                      <th className="pr-2">Difficulty</th>
                      <th className="pr-2">Need</th>
                      <th className="pr-2">Have</th>
                      <th>Short</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bp.generation_result.shortages.map((s, i) => (
                      <tr key={i}>
                        <td className="py-0.5 pr-2">{s.chapter ?? "Any"}</td>
                        <td className="pr-2">{s.topic ?? "Any"}</td>
                        <td className="pr-2">{s.subtopic ?? "Any"}</td>
                        <td className="pr-2 capitalize">{s.complexity ?? "Any"}</td>
                        <td className="pr-2 tabular-nums">{s.required}</td>
                        <td className="pr-2 tabular-nums">{s.available}</td>
                        <td className="font-semibold tabular-nums">{s.shortage}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button onClick={() => onRegenerate(bp.id)} className="mt-2 text-xs font-medium text-brand-600 hover:text-brand-700">
                Retry generation
              </button>
            </div>
          ) : null}

          {bp.status === "generated" && (
            <p className="mt-2 text-sm text-success-700">
              {bp.generation_result?.sets_created ?? bp.num_sets} set(s) generated — review them in the <strong>Generated Sets</strong> tab.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Sets table ────────────────────────────────────────────────────────────────

function SetsTable({
  sets,
  onPreview,
  onAction,
}: {
  sets: TestSet[];
  onPreview: (id: string) => void;
  onAction: (id: string, action: "activate" | "deactivate" | "archive" | "delete") => void;
}) {
  const columns: Column<TestSet>[] = [
    { key: "set_name", header: "Set", accessor: (s) => s.set_name, render: (s) => <span className="font-medium text-gray-900">{s.set_name}</span> },
    { key: "num_questions", header: "Questions", align: "right", render: (s) => <span className="tabular-nums">{s.num_questions}</span> },
    {
      key: "difficulty_mix",
      header: "Difficulty mix",
      render: (s) => (
        <span className="text-xs text-gray-500">
          {s.difficulty_mix ? Object.entries(s.difficulty_mix).map(([k, v]) => `${k}:${v}`).join("  ") : "—"}
        </span>
      ),
    },
    { key: "status", header: "Status", render: (s) => <StatusBadge status={s.status} /> },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "8rem",
      render: (s) => (
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" size="xs" onClick={() => onPreview(s.id)}>
            Preview
          </Button>
          <Menu
            items={[
              ...(s.status !== "active" ? [{ label: "Activate", onClick: () => onAction(s.id, "activate") }] : []),
              ...(s.status === "active" ? [{ label: "Deactivate", onClick: () => onAction(s.id, "deactivate") }] : []),
              ...(s.status !== "archived" ? [{ label: "Archive", onClick: () => onAction(s.id, "archive") }] : []),
              { label: "Delete", tone: "danger" as const, onClick: () => onAction(s.id, "delete") },
            ]}
          />
        </div>
      ),
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={sets}
      rowKey={(s) => s.id}
      emptyState={<EmptyState icon={<FileText className="h-5 w-5" />} title="No sets here" className="border-0" />}
    />
  );
}

// ── Preview modal ─────────────────────────────────────────────────────────────

function PreviewModal({ preview, onClose }: { preview: TestSetPreview; onClose: () => void }) {
  return (
    <Modal open onClose={onClose} size="lg" title={preview.set_name} subtitle={`${preview.num_questions} questions · ${preview.total_time_minutes} min`}>
      <div className="space-y-4">
        {preview.questions.map((q, i) => (
          <div key={q.id} className="rounded-md border border-gray-200 p-3">
            <p className="text-sm font-medium text-gray-900">
              {i + 1}. {q.question_text}
            </p>
            <ul className="mt-2 space-y-1">
              {q.options.map((o) => {
                const correct = q.correct_option_ids.includes(o.id);
                return (
                  <li key={o.id} className={`flex items-center gap-1.5 text-sm ${correct ? "font-medium text-success-700" : "text-gray-600"}`}>
                    <span>
                      {o.label}. {o.text}
                    </span>
                    {correct && <Check className="h-4 w-4 flex-shrink-0" />}
                  </li>
                );
              })}
            </ul>
            {q.explanation && <p className="mt-2 text-xs text-gray-500">Explanation: {q.explanation}</p>}
          </div>
        ))}
      </div>
    </Modal>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function AdminMCQTests() {
  const { selectedExamId } = useExam();
  const [tab, setTab] = useState<Tab>("create");
  const [chapters, setChapters] = useState<ChapterNode[]>([]);
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [sets, setSets] = useState<TestSet[]>([]);
  const [preview, setPreview] = useState<TestSetPreview | null>(null);
  const [error, setError] = useState("");
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedExamId) {
      setChapters([]);
      return;
    }
    syllabusService.get(selectedExamId).then((t) => setChapters(t.chapters)).catch(() => setChapters([]));
  }, [selectedExamId]);

  const refreshBlueprints = useCallback(async () => {
    try {
      const res = await mcqTestsService.listBlueprints(1, 50);
      setBlueprints(res.items);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  const refreshSets = useCallback(async () => {
    try {
      const res = await mcqTestsService.listSets({ per_page: 100 });
      setSets(res.items);
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    refreshBlueprints();
    refreshSets();
  }, [refreshBlueprints, refreshSets]);

  async function handlePreview(id: string) {
    try {
      setPreview(await mcqTestsService.previewSet(id));
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function handleSetAction(id: string, action: "activate" | "deactivate" | "archive" | "delete") {
    setError("");
    if (action === "delete") {
      setConfirmDelId(id);
      return;
    }
    try {
      if (action === "activate") await mcqTestsService.activateSet(id);
      else if (action === "deactivate") await mcqTestsService.deactivateSet(id);
      else if (action === "archive") await mcqTestsService.archiveSet(id);
      await refreshSets();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function confirmDelete() {
    if (!confirmDelId) return;
    try {
      await mcqTestsService.deleteSet(confirmDelId);
      setConfirmDelId(null);
      await refreshSets();
    } catch (err) {
      setError(getErrorMessage(err));
      setConfirmDelId(null);
    }
  }

  async function handleRegenerate(id: string) {
    try {
      await mcqTestsService.regenerateBlueprint(id);
      await refreshBlueprints();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  const draftSets = sets.filter((s) => s.status !== "active" && s.status !== "archived");
  const activeSets = sets.filter((s) => s.status === "active");

  const TABS = [
    { id: "create", label: "Create Blueprint" },
    { id: "sets", label: "Generated Sets" },
    { id: "active", label: "Active Tests" },
    { id: "attempts", label: "Student Attempts" },
    { id: "analytics", label: "MCQ Analytics" },
  ];

  return (
    <div>
      <PageHeader title="MCQ Tests" icon={<FileText className="h-5 w-5" />} />

      <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} className="mb-6" />

      {error && <Alert className="mb-4">{error}</Alert>}

      {tab === "create" && (
        <div className="space-y-8">
          <CreateBlueprintTab
            chapters={chapters}
            onGenerated={() => {
              refreshBlueprints();
              refreshSets();
            }}
          />
          <div>
            <h2 className="mb-3 text-sm font-semibold text-gray-900">Recent Blueprints</h2>
            <BlueprintList blueprints={blueprints} onRegenerate={handleRegenerate} />
          </div>
        </div>
      )}

      {tab === "sets" && <SetsTable sets={draftSets} onPreview={handlePreview} onAction={handleSetAction} />}
      {tab === "active" && <SetsTable sets={activeSets} onPreview={handlePreview} onAction={handleSetAction} />}

      {tab === "attempts" && <MCQAnalyticsView />}
      {tab === "analytics" && <MCQAnalyticsView />}

      {preview && <PreviewModal preview={preview} onClose={() => setPreview(null)} />}

      <ConfirmDialog
        open={!!confirmDelId}
        title="Delete set"
        description="Delete this set permanently? This cannot be undone."
        confirmLabel="Delete"
        tone="danger"
        onConfirm={confirmDelete}
        onClose={() => setConfirmDelId(null)}
      />
    </div>
  );
}
