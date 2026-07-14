import React, { useEffect, useRef, useState } from "react";
import { ListTree, Upload, X, Plus } from "lucide-react";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import { useExam } from "../../context/ExamContext";
import { PageHeader, Button, Alert, ConfirmDialog, EmptyState } from "../../components/ui";

export function AdminSyllabus() {
  const { selectedExamId, selectedExam, exams } = useExam();
  const [tree, setTree] = useState<SyllabusTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [importJobId, setImportJobId] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    if (!selectedExamId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const t = await syllabusService.get(selectedExamId);
    setTree(t);
    setLoading(false);
  };

  useEffect(() => {
    load(); /* eslint-disable-next-line */
  }, [selectedExamId]);

  const update = (newTree: SyllabusTree) => setTree(newTree);

  const startImport = async (file: File) => {
    if (!selectedExamId) return;
    setImportError(null);
    try {
      const { job_id } = await syllabusService.importFromPdf(selectedExamId, file);
      setImportJobId(job_id);
    } catch {
      setImportError("Could not upload the syllabus file. Check the file type (PDF/Word) and size.");
    }
  };

  const onPickImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = "";
    if (!file || !selectedExamId) return;
    const hasSyllabus = (tree?.chapters.length ?? 0) > 0;
    if (hasSyllabus) {
      setPendingFile(file);
    } else {
      startImport(file);
    }
  };

  if (!selectedExamId) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-gray-400">
        {exams.length === 0 ? "Create an exam first (Exams tab)." : "Select an exam in the top bar."}
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="Syllabus"
        description={
          <>
            Editing <span className="font-medium text-gray-700">{selectedExam?.name}</span> ({selectedExam?.exam_type}).
            Add, rename, or delete any chapter / topic / subtopic.
          </>
        }
        icon={<ListTree className="h-5 w-5" />}
        actions={
          <>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.doc" onChange={onPickImportFile} className="hidden" />
            <Button variant="secondary" icon={<Upload className="h-4 w-4" />} onClick={() => fileRef.current?.click()}>
              Import from PDF
            </Button>
          </>
        }
      />

      {importError && <Alert className="mb-3">{importError}</Alert>}
      {importJobId && (
        <JobStatusPoller
          jobId={importJobId}
          className="mb-4"
          onComplete={() => {
            setImportJobId(null);
            load();
          }}
          onFail={() => setImportError("Syllabus extraction failed. Try another file or add the syllabus manually.")}
        />
      )}

      {loading ? (
        <div className="flex h-48 items-center justify-center text-sm text-gray-400">Loading…</div>
      ) : (
        <div className="space-y-4">
          {tree?.chapters.map((ch) => (
            <ChapterBlock key={ch.chapter} chapter={ch} examId={selectedExamId} onUpdate={update} />
          ))}
          {(tree?.chapters.length ?? 0) === 0 && (
            <EmptyState icon={<ListTree className="h-5 w-5" />} title="No chapters yet" description="Add a chapter below or import a syllabus file." />
          )}
          <AddChapterRow examId={selectedExamId} onUpdate={update} />
        </div>
      )}

      <ConfirmDialog
        open={!!pendingFile}
        title="Replace syllabus"
        description="Importing a syllabus file REPLACES the entire current syllabus for this exam. Continue?"
        confirmLabel="Replace"
        tone="danger"
        onConfirm={() => {
          if (pendingFile) startImport(pendingFile);
          setPendingFile(null);
        }}
        onClose={() => setPendingFile(null)}
      />
    </div>
  );
}

// ── Chapter block ──────────────────────────────────────────────────────────────

function ChapterBlock({
  chapter,
  examId,
  onUpdate,
}: {
  chapter: { chapter: string; topics: { topic: string; subtopics: { id: string; subtopic: string }[] }[] };
  examId: string;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(chapter.chapter);
  const [saving, setSaving] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);

  const saveRename = async () => {
    if (name.trim() === chapter.chapter || !name.trim()) {
      setEditing(false);
      return;
    }
    setSaving(true);
    const tree = await syllabusService.renameChapter(examId, chapter.chapter, name.trim());
    onUpdate(tree);
    setSaving(false);
    setEditing(false);
  };

  const handleDelete = async () => {
    const tree = await syllabusService.deleteChapter(examId, chapter.chapter);
    onUpdate(tree);
    setConfirmDel(false);
  };

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <div className="flex items-center gap-2 border-b border-gray-200 bg-gray-50 px-5 py-3">
        {editing ? (
          <>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename();
                if (e.key === "Escape") {
                  setEditing(false);
                  setName(chapter.chapter);
                }
              }}
              className={inputCls + " flex-1 font-semibold"}
            />
            <Button onClick={saveRename} loading={saving} size="xs">
              Save
            </Button>
            <Button
              onClick={() => {
                setEditing(false);
                setName(chapter.chapter);
              }}
              variant="ghost"
              size="xs"
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <h3 className="flex-1 font-semibold text-gray-900">{chapter.chapter}</h3>
            <Button onClick={() => setEditing(true)} variant="ghost" size="xs">
              Rename
            </Button>
            <Button onClick={() => setConfirmDel(true)} variant="danger" size="xs">
              Delete Chapter
            </Button>
          </>
        )}
      </div>

      <div className="divide-y divide-gray-100 px-5">
        {chapter.topics.map((topic) => (
          <TopicBlock key={topic.topic} chapterName={chapter.chapter} topic={topic} examId={examId} onUpdate={onUpdate} />
        ))}
        <AddTopicRow chapterName={chapter.chapter} examId={examId} onUpdate={onUpdate} />
      </div>

      <ConfirmDialog
        open={confirmDel}
        title="Delete chapter"
        description={`Delete chapter "${chapter.chapter}" and all its topics/subtopics?`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
        onClose={() => setConfirmDel(false)}
      />
    </div>
  );
}

// ── Topic block ────────────────────────────────────────────────────────────────

function TopicBlock({
  chapterName,
  topic,
  examId,
  onUpdate,
}: {
  chapterName: string;
  topic: { topic: string; subtopics: { id: string; subtopic: string }[] };
  examId: string;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(topic.topic);
  const [confirmDel, setConfirmDel] = useState(false);

  const saveRename = async () => {
    if (name.trim() === topic.topic || !name.trim()) {
      setEditing(false);
      return;
    }
    const tree = await syllabusService.renameTopic(examId, chapterName, topic.topic, name.trim());
    onUpdate(tree);
    setEditing(false);
  };

  const handleDelete = async () => {
    const tree = await syllabusService.deleteTopic(examId, chapterName, topic.topic);
    onUpdate(tree);
    setConfirmDel(false);
  };

  return (
    <div className="py-3">
      <div className="flex items-center gap-2">
        {editing ? (
          <>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveRename();
                if (e.key === "Escape") {
                  setEditing(false);
                  setName(topic.topic);
                }
              }}
              className={inputCls + " flex-1 font-medium"}
            />
            <Button onClick={saveRename} size="xs">
              Save
            </Button>
            <Button
              onClick={() => {
                setEditing(false);
                setName(topic.topic);
              }}
              variant="ghost"
              size="xs"
            >
              Cancel
            </Button>
          </>
        ) : (
          <>
            <p className="flex-1 text-sm font-medium text-gray-800">{topic.topic}</p>
            <Button onClick={() => setEditing(true)} variant="ghost" size="xs">
              Rename
            </Button>
            <Button onClick={() => setConfirmDel(true)} variant="danger" size="xs">
              Delete
            </Button>
          </>
        )}
      </div>

      <ul className="mt-2 space-y-1 pl-4">
        {topic.subtopics.map((sub) => (
          <SubtopicRow key={sub.id} entry={sub} onUpdate={onUpdate} />
        ))}
        <AddSubtopicRow chapterName={chapterName} topicName={topic.topic} examId={examId} onUpdate={onUpdate} />
      </ul>

      <ConfirmDialog
        open={confirmDel}
        title="Delete topic"
        description={`Delete topic "${topic.topic}" and all its subtopics?`}
        confirmLabel="Delete"
        tone="danger"
        onConfirm={handleDelete}
        onClose={() => setConfirmDel(false)}
      />
    </div>
  );
}

// ── Subtopic row ───────────────────────────────────────────────────────────────

function SubtopicRow({ entry, onUpdate }: { entry: { id: string; subtopic: string }; onUpdate: (t: SyllabusTree) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(entry.subtopic);

  const save = async () => {
    if (name.trim() === entry.subtopic || !name.trim()) {
      setEditing(false);
      return;
    }
    const tree = await syllabusService.updateItem(entry.id, { subtopic: name.trim() });
    onUpdate(tree);
    setEditing(false);
  };

  const del = async () => {
    const tree = await syllabusService.deleteItem(entry.id);
    onUpdate(tree);
  };

  return (
    <li className="flex items-center gap-2">
      <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-gray-300" />
      {editing ? (
        <>
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") {
                setEditing(false);
                setName(entry.subtopic);
              }
            }}
            className={inputCls + " flex-1"}
          />
          <Button onClick={save} size="xs">
            Save
          </Button>
          <Button onClick={() => { setEditing(false); setName(entry.subtopic); }} variant="ghost" size="xs" icon={<X className="h-3.5 w-3.5" />} aria-label="Cancel" />
        </>
      ) : (
        <>
          <span className="flex-1 text-sm text-gray-600">{entry.subtopic}</span>
          <Button onClick={() => setEditing(true)} variant="ghost" size="xs">
            Edit
          </Button>
          <Button onClick={del} variant="ghost" size="xs" className="text-danger-600 hover:bg-danger-50" icon={<X className="h-3.5 w-3.5" />} aria-label="Delete" />
        </>
      )}
    </li>
  );
}

// ── Add rows ───────────────────────────────────────────────────────────────────

function AddSubtopicRow({
  chapterName,
  topicName,
  examId,
  onUpdate,
}: {
  chapterName: string;
  topicName: string;
  examId: string;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const save = async () => {
    if (!val.trim()) return;
    const tree = await syllabusService.addItem(examId, chapterName, topicName, val.trim());
    onUpdate(tree);
    setVal("");
    setOpen(false);
  };
  return (
    <li className="flex items-center gap-2 pt-1">
      {open ? (
        <>
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setOpen(false);
            }}
            placeholder="Subtopic name…"
            className={inputCls + " flex-1"}
          />
          <Button onClick={save} size="xs">
            Add
          </Button>
          <Button onClick={() => setOpen(false)} variant="ghost" size="xs" icon={<X className="h-3.5 w-3.5" />} aria-label="Cancel" />
        </>
      ) : (
        <button onClick={() => setOpen(true)} className="text-xs font-medium text-brand-600 hover:text-brand-700">
          + Add subtopic
        </button>
      )}
    </li>
  );
}

function AddTopicRow({ chapterName, examId, onUpdate }: { chapterName: string; examId: string; onUpdate: (t: SyllabusTree) => void }) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const save = async () => {
    if (!val.trim()) return;
    const tree = await syllabusService.addItem(examId, chapterName, val.trim());
    onUpdate(tree);
    setVal("");
    setOpen(false);
  };
  return (
    <div className="py-2">
      {open ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={val}
            onChange={(e) => setVal(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
              if (e.key === "Escape") setOpen(false);
            }}
            placeholder="Topic name…"
            className={inputCls + " flex-1"}
          />
          <Button onClick={save} size="xs">
            Add
          </Button>
          <Button onClick={() => setOpen(false)} variant="ghost" size="xs" icon={<X className="h-3.5 w-3.5" />} aria-label="Cancel" />
        </div>
      ) : (
        <button onClick={() => setOpen(true)} className="text-sm font-medium text-brand-600 hover:text-brand-700">
          + Add topic
        </button>
      )}
    </div>
  );
}

function AddChapterRow({ examId, onUpdate }: { examId: string; onUpdate: (t: SyllabusTree) => void }) {
  const [open, setOpen] = useState(false);
  const [chapter, setChapter] = useState("");
  const [topic, setTopic] = useState("");
  const save = async () => {
    if (!chapter.trim() || !topic.trim()) return;
    const tree = await syllabusService.addItem(examId, chapter.trim(), topic.trim());
    onUpdate(tree);
    setChapter("");
    setTopic("");
    setOpen(false);
  };
  return (
    <div>
      {open ? (
        <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
          <p className="text-sm font-semibold text-gray-900">New Chapter</p>
          <input value={chapter} onChange={(e) => setChapter(e.target.value)} placeholder="Chapter name…" className={inputCls + " w-full"} />
          <input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="First topic name (required)…" className={inputCls + " w-full"} />
          <div className="flex gap-2">
            <Button onClick={save} size="sm">
              Add Chapter
            </Button>
            <Button onClick={() => setOpen(false)} variant="ghost" size="sm">
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-gray-300 py-3 text-sm text-gray-500 transition-colors hover:border-brand-400 hover:text-brand-600"
        >
          <Plus className="h-4 w-4" />
          Add Chapter
        </button>
      )}
    </div>
  );
}

const inputCls =
  "rounded-md border border-gray-300 px-2.5 py-1.5 text-sm outline-none transition-colors focus:border-brand-600 focus:ring-2 focus:ring-brand-600/20";
