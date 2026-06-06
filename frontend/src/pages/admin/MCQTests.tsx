import { useCallback, useEffect, useMemo, useState } from "react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import type { JobState } from "../../components/JobStatusPoller";
import { mcqTestsService } from "../../services/mcqTests";
import type { Blueprint, TestSet, TestSetPreview, TopicDistEntry } from "../../services/mcqTests";
import { syllabusService } from "../../services/syllabus";
import type { ChapterNode } from "../../services/syllabus";
import { getErrorMessage } from "../../utils/error";
import { MCQAnalyticsView } from "./Analytics";

type Tab = "create" | "sets" | "active" | "attempts" | "analytics";

const SET_STATUS_BADGE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  active: "bg-green-100 text-green-700",
  archived: "bg-gray-200 text-gray-500",
};
const BP_STATUS_BADGE: Record<string, string> = {
  draft: "bg-gray-100 text-gray-600",
  generating: "bg-blue-100 text-blue-700",
  generated: "bg-green-100 text-green-700",
  shortage: "bg-red-100 text-red-700",
};

// ── Create Blueprint Tab ──────────────────────────────────────────────────────

function CreateBlueprintTab({ chapters, onGenerated }: { chapters: ChapterNode[]; onGenerated: () => void }) {
  const allTopics = useMemo(() => chapters.flatMap((c) => c.topics), [chapters]);

  const [testName, setTestName] = useState("");
  const [totalTime, setTotalTime] = useState("60");
  const [numSets, setNumSets] = useState("1");
  const [customInstruction, setCustomInstruction] = useState("");
  const [rows, setRows] = useState<TopicDistEntry[]>([{ topic: "", subtopic: "", count: 5 }]);
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
    setRows((prev) => [...prev, { topic: "", subtopic: "", count: 5 }]);
  }
  function removeRow(i: number) {
    setRows((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!testName.trim()) return setError("Test name is required.");
    if (rows.length === 0 || perSetTotal === 0) return setError("Add at least one topic row with a question count.");
    if (useDifficulty && diffTotal > perSetTotal)
      return setError("Difficulty totals cannot exceed total questions per set.");

    setLoading(true);
    try {
      const job = await mcqTestsService.createBlueprint({
        test_name: testName.trim(),
        total_time_minutes: Number(totalTime),
        num_sets: Number(numSets),
        topic_distribution: rows.map((r) => ({
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
        <div className="sm:col-span-3">
          <label className="mb-1 block text-sm font-medium text-gray-700">Test Name *</label>
          <input className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" value={testName}
            onChange={(e) => setTestName(e.target.value)} placeholder="e.g. Banking Practice Test 1" />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Total Time (min) *</label>
          <input type="number" min={1} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={totalTime} onChange={(e) => setTotalTime(e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Number of Sets *</label>
          <input type="number" min={1} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            value={numSets} onChange={(e) => setNumSets(e.target.value)} />
        </div>
        <div className="flex items-end text-sm text-gray-500">{perSetTotal} questions / set</div>
      </div>

      {/* Topic / subtopic distribution */}
      <div>
        <label className="mb-2 block text-sm font-medium text-gray-700">Topic / Subtopic Distribution *</label>
        <div className="space-y-2">
          {rows.map((row, i) => {
            const subtopics = allTopics.find((t) => t.topic === row.topic)?.subtopics ?? [];
            return (
              <div key={i} className="grid grid-cols-[1fr_1fr_90px_auto] gap-2">
                <select aria-label="Topic" className="rounded-lg border border-gray-300 px-2 py-2 text-sm bg-white"
                  value={row.topic ?? ""} onChange={(e) => updateRow(i, { topic: e.target.value, subtopic: "" })}>
                  <option value="">Any topic</option>
                  {allTopics.map((t) => <option key={t.topic} value={t.topic}>{t.topic}</option>)}
                </select>
                <select aria-label="Subtopic" className="rounded-lg border border-gray-300 px-2 py-2 text-sm bg-white"
                  value={row.subtopic ?? ""} onChange={(e) => updateRow(i, { subtopic: e.target.value })}
                  disabled={subtopics.length === 0}>
                  <option value="">Any subtopic</option>
                  {subtopics.map((s) => <option key={s.id} value={s.subtopic}>{s.subtopic}</option>)}
                </select>
                <input type="number" min={1} aria-label="Count" className="rounded-lg border border-gray-300 px-2 py-2 text-sm"
                  value={row.count} onChange={(e) => updateRow(i, { count: Number(e.target.value) })} />
                <button type="button" onClick={() => removeRow(i)} disabled={rows.length === 1}
                  className="rounded-lg px-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-30">✕</button>
              </div>
            );
          })}
        </div>
        <button type="button" onClick={addRow} className="mt-2 text-sm font-medium text-brand-600 hover:text-brand-700">
          + Add topic row
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
              <div key={d}>
                <label className="mb-1 block text-xs capitalize text-gray-500">{d}</label>
                <input type="number" min={0} className="w-full rounded-lg border border-gray-300 px-2 py-1.5 text-sm"
                  value={difficulty[d]} onChange={(e) => setDifficulty((p) => ({ ...p, [d]: Number(e.target.value) }))} />
              </div>
            ))}
            <p className={`col-span-3 text-xs ${diffTotal > perSetTotal ? "text-red-600" : "text-gray-400"}`}>
              {diffTotal} of {perSetTotal} questions assigned a difficulty{diffTotal > perSetTotal ? " — exceeds total!" : ""}
            </p>
          </div>
        )}
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Custom Instruction (optional)</label>
        <textarea rows={2} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          value={customInstruction} onChange={(e) => setCustomInstruction(e.target.value)} />
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={loading}
        className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50">
        {loading ? "Submitting…" : "Generate Test Sets"}
      </button>

      {jobId && (
        <JobStatusPoller jobId={jobId} className="mt-4 max-w-md" onComplete={handleJobComplete} onFail={handleJobComplete} />
      )}
    </form>
  );
}

// ── Blueprint list (shows shortage breakdown) ─────────────────────────────────

function BlueprintList({ blueprints, onRegenerate }: { blueprints: Blueprint[]; onRegenerate: (id: string) => void }) {
  if (blueprints.length === 0) return <p className="text-sm text-gray-500">No blueprints yet.</p>;
  return (
    <div className="space-y-3">
      {blueprints.map((bp) => (
        <div key={bp.id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
          <div className="flex items-start justify-between">
            <div>
              <p className="font-medium text-gray-900">{bp.test_name}</p>
              <p className="text-xs text-gray-500">
                {bp.num_sets} set(s) · {bp.total_time_minutes} min · created {new Date(bp.created_at).toLocaleString()}
              </p>
            </div>
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${BP_STATUS_BADGE[bp.status] ?? "bg-gray-100 text-gray-600"}`}>
              {bp.status}
            </span>
          </div>

          {bp.status === "shortage" && bp.generation_result?.shortages?.length ? (
            <div className="mt-3 rounded-lg bg-red-50 p-3">
              <p className="text-sm font-medium text-red-700">Not enough approved questions — no sets were created.</p>
              <table className="mt-2 w-full text-xs text-red-800">
                <thead>
                  <tr className="text-left text-red-500">
                    <th className="py-1 pr-2">Topic</th><th className="pr-2">Subtopic</th><th className="pr-2">Difficulty</th>
                    <th className="pr-2">Need</th><th className="pr-2">Have</th><th>Short</th>
                  </tr>
                </thead>
                <tbody>
                  {bp.generation_result.shortages.map((s, i) => (
                    <tr key={i}>
                      <td className="py-0.5 pr-2">{s.topic ?? "Any"}</td>
                      <td className="pr-2">{s.subtopic ?? "Any"}</td>
                      <td className="pr-2 capitalize">{s.complexity ?? "Any"}</td>
                      <td className="pr-2">{s.required}</td>
                      <td className="pr-2">{s.available}</td>
                      <td className="font-semibold">{s.shortage}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button onClick={() => onRegenerate(bp.id)} className="mt-2 text-xs font-medium text-brand-600 hover:text-brand-700">
                Retry generation
              </button>
            </div>
          ) : null}

          {bp.status === "generated" && (
            <p className="mt-2 text-sm text-green-700">
              {bp.generation_result?.sets_created ?? bp.num_sets} set(s) generated — review them in the <strong>Generated Sets</strong> tab.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Sets table ────────────────────────────────────────────────────────────────

function SetsTable({ sets, onPreview, onAction }: {
  sets: TestSet[];
  onPreview: (id: string) => void;
  onAction: (id: string, action: "activate" | "deactivate" | "archive" | "delete") => void;
}) {
  if (sets.length === 0) return <p className="text-sm text-gray-500">No sets here.</p>;
  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-left text-xs text-gray-500">
          <tr>
            <th className="px-4 py-2">Set</th><th className="px-4 py-2">Questions</th>
            <th className="px-4 py-2">Difficulty mix</th><th className="px-4 py-2">Status</th><th className="px-4 py-2">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {sets.map((s) => (
            <tr key={s.id}>
              <td className="px-4 py-2 text-gray-900">{s.set_name}</td>
              <td className="px-4 py-2">{s.num_questions}</td>
              <td className="px-4 py-2 text-xs text-gray-500">
                {s.difficulty_mix ? Object.entries(s.difficulty_mix).map(([k, v]) => `${k}:${v}`).join("  ") : "—"}
              </td>
              <td className="px-4 py-2">
                <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${SET_STATUS_BADGE[s.status] ?? "bg-gray-100 text-gray-600"}`}>
                  {s.status}
                </span>
              </td>
              <td className="px-4 py-2">
                <div className="flex gap-2 text-xs font-medium">
                  <button onClick={() => onPreview(s.id)} className="text-brand-600 hover:text-brand-700">Preview</button>
                  {s.status !== "active" && <button onClick={() => onAction(s.id, "activate")} className="text-green-600 hover:text-green-700">Activate</button>}
                  {s.status === "active" && <button onClick={() => onAction(s.id, "deactivate")} className="text-yellow-600 hover:text-yellow-700">Deactivate</button>}
                  {s.status !== "archived" && <button onClick={() => onAction(s.id, "archive")} className="text-gray-500 hover:text-gray-700">Archive</button>}
                  <button onClick={() => onAction(s.id, "delete")} className="text-red-600 hover:text-red-700">Delete</button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Preview modal ─────────────────────────────────────────────────────────────

function PreviewModal({ preview, onClose }: { preview: TestSetPreview; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center overflow-y-auto bg-black/40 p-6" onClick={onClose}>
      <div className="w-full max-w-2xl rounded-xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-start justify-between">
          <div>
            <h3 className="text-lg font-semibold text-gray-900">{preview.set_name}</h3>
            <p className="text-xs text-gray-500">{preview.num_questions} questions · {preview.total_time_minutes} min</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        <div className="space-y-4">
          {preview.questions.map((q, i) => (
            <div key={q.id} className="rounded-lg border border-gray-100 p-3">
              <p className="text-sm font-medium text-gray-900">{i + 1}. {q.question_text}</p>
              <ul className="mt-2 space-y-1">
                {q.options.map((o) => {
                  const correct = q.correct_option_ids.includes(o.id);
                  return (
                    <li key={o.id} className={`text-sm ${correct ? "font-medium text-green-700" : "text-gray-600"}`}>
                      {o.label}. {o.text} {correct && "✓"}
                    </li>
                  );
                })}
              </ul>
              {q.explanation && <p className="mt-2 text-xs text-gray-500">Explanation: {q.explanation}</p>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function AdminMCQTests() {
  const [tab, setTab] = useState<Tab>("create");
  const [chapters, setChapters] = useState<ChapterNode[]>([]);
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [sets, setSets] = useState<TestSet[]>([]);
  const [preview, setPreview] = useState<TestSetPreview | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    syllabusService.getObjective().then((t) => setChapters(t.chapters)).catch(() => setChapters([]));
  }, []);

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
    try {
      if (action === "delete") {
        if (!confirm("Delete this set permanently?")) return;
        await mcqTestsService.deleteSet(id);
      } else if (action === "activate") await mcqTestsService.activateSet(id);
      else if (action === "deactivate") await mcqTestsService.deactivateSet(id);
      else if (action === "archive") await mcqTestsService.archiveSet(id);
      await refreshSets();
    } catch (err) {
      setError(getErrorMessage(err));
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

  const TABS: { key: Tab; label: string }[] = [
    { key: "create", label: "Create Blueprint" },
    { key: "sets", label: "Generated Sets" },
    { key: "active", label: "Active Tests" },
    { key: "attempts", label: "Student Attempts" },
    { key: "analytics", label: "MCQ Analytics" },
  ];

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold text-gray-900">MCQ Tests</h1>

      <div className="mb-6 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.key ? "border-b-2 border-brand-600 text-brand-700" : "text-gray-500 hover:text-gray-700"
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

      {tab === "create" && (
        <div className="space-y-8">
          <CreateBlueprintTab chapters={chapters} onGenerated={() => { refreshBlueprints(); refreshSets(); }} />
          <div>
            <h2 className="mb-3 text-sm font-semibold text-gray-700">Recent Blueprints</h2>
            <BlueprintList blueprints={blueprints} onRegenerate={handleRegenerate} />
          </div>
        </div>
      )}

      {tab === "sets" && <SetsTable sets={draftSets} onPreview={handlePreview} onAction={handleSetAction} />}
      {tab === "active" && <SetsTable sets={activeSets} onPreview={handlePreview} onAction={handleSetAction} />}

      {tab === "attempts" && <MCQAnalyticsView />}
      {tab === "analytics" && <MCQAnalyticsView />}

      {preview && <PreviewModal preview={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
