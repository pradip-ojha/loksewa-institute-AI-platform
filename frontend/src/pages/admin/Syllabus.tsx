import { useEffect, useRef, useState } from "react";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";

type SyllabusTypeKey = "objective" | "subjective";

export function AdminSyllabus() {
  const [activeTab, setActiveTab] = useState<SyllabusTypeKey>("objective");
  const [trees, setTrees] = useState<Record<SyllabusTypeKey, SyllabusTree | null>>({ objective: null, subjective: null });
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    const [obj, subj] = await Promise.all([syllabusService.getObjective(), syllabusService.getSubjective()]);
    setTrees({ objective: obj, subjective: subj });
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const tree = trees[activeTab];

  const update = (newTree: SyllabusTree) =>
    setTrees(prev => ({ ...prev, [activeTab]: newTree }));

  return (
    <div>
      <div className="mb-6 flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Syllabus</h2>
          <p className="mt-1 text-sm text-gray-500">
            Seeded from system defaults. You can add, rename, or delete any item.
          </p>
        </div>
      </div>

      <div className="mb-5 flex gap-1 rounded-xl bg-gray-100 p-1 w-fit">
        {(["objective", "subjective"] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`rounded-lg px-5 py-2 text-sm font-medium capitalize transition-colors ${
              activeTab === tab ? "bg-white text-gray-900 shadow-sm" : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {tab} Syllabus
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex h-48 items-center justify-center text-sm text-gray-400">Loading…</div>
      ) : (
        <div className="space-y-4">
          {tree?.chapters.map(ch => (
            <ChapterBlock
              key={ch.chapter}
              chapter={ch}
              syllabusType={activeTab}
              onUpdate={update}
            />
          ))}

          <AddChapterRow syllabusType={activeTab} onUpdate={update} />
        </div>
      )}
    </div>
  );
}

// ── Chapter block ──────────────────────────────────────────────────────────────

function ChapterBlock({ chapter, syllabusType, onUpdate }: {
  chapter: { chapter: string; topics: { topic: string; subtopics: { id: string; subtopic: string }[] }[] };
  syllabusType: SyllabusTypeKey;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(chapter.chapter);
  const [saving, setSaving] = useState(false);

  const saveRename = async () => {
    if (name.trim() === chapter.chapter || !name.trim()) { setEditing(false); return; }
    setSaving(true);
    const tree = await syllabusService.renameChapter(syllabusType, chapter.chapter, name.trim());
    onUpdate(tree);
    setSaving(false);
    setEditing(false);
  };

  const handleDelete = async () => {
    if (!confirm(`Delete chapter "${chapter.chapter}" and all its topics/subtopics?`)) return;
    const tree = await syllabusService.deleteChapter(syllabusType, chapter.chapter);
    onUpdate(tree);
  };

  return (
    <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
      {/* Chapter header */}
      <div className="flex items-center gap-2 border-b border-gray-100 bg-brand-50 px-5 py-3">
        {editing ? (
          <>
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") { setEditing(false); setName(chapter.chapter); } }}
              className="flex-1 rounded-lg border border-brand-300 px-2 py-1 text-sm font-semibold outline-none focus:ring-2 focus:ring-brand-200"
            />
            <Btn onClick={saveRename} disabled={saving} variant="primary" size="xs">{saving ? "Saving…" : "Save"}</Btn>
            <Btn onClick={() => { setEditing(false); setName(chapter.chapter); }} variant="ghost" size="xs">Cancel</Btn>
          </>
        ) : (
          <>
            <h3 className="flex-1 font-semibold text-brand-800">{chapter.chapter}</h3>
            <Btn onClick={() => setEditing(true)} variant="ghost" size="xs">Rename</Btn>
            <Btn onClick={handleDelete} variant="danger" size="xs">Delete Chapter</Btn>
          </>
        )}
      </div>

      {/* Topics */}
      <div className="divide-y divide-gray-50 px-5">
        {chapter.topics.map(topic => (
          <TopicBlock
            key={topic.topic}
            chapterName={chapter.chapter}
            topic={topic}
            syllabusType={syllabusType}
            onUpdate={onUpdate}
          />
        ))}
        <AddTopicRow chapterName={chapter.chapter} syllabusType={syllabusType} onUpdate={onUpdate} />
      </div>
    </div>
  );
}

// ── Topic block ────────────────────────────────────────────────────────────────

function TopicBlock({ chapterName, topic, syllabusType, onUpdate }: {
  chapterName: string;
  topic: { topic: string; subtopics: { id: string; subtopic: string }[] };
  syllabusType: SyllabusTypeKey;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(topic.topic);

  const saveRename = async () => {
    if (name.trim() === topic.topic || !name.trim()) { setEditing(false); return; }
    const tree = await syllabusService.renameTopic(syllabusType, chapterName, topic.topic, name.trim());
    onUpdate(tree);
    setEditing(false);
  };

  const handleDelete = async () => {
    if (!confirm(`Delete topic "${topic.topic}" and all its subtopics?`)) return;
    const tree = await syllabusService.deleteTopic(syllabusType, chapterName, topic.topic);
    onUpdate(tree);
  };

  return (
    <div className="py-3">
      <div className="flex items-center gap-2">
        {editing ? (
          <>
            <input
              autoFocus value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") saveRename(); if (e.key === "Escape") { setEditing(false); setName(topic.topic); } }}
              className="flex-1 rounded-lg border border-gray-300 px-2 py-1 text-sm font-medium outline-none focus:ring-2 focus:ring-brand-100"
            />
            <Btn onClick={saveRename} variant="primary" size="xs">Save</Btn>
            <Btn onClick={() => { setEditing(false); setName(topic.topic); }} variant="ghost" size="xs">Cancel</Btn>
          </>
        ) : (
          <>
            <p className="flex-1 text-sm font-medium text-gray-800">{topic.topic}</p>
            <Btn onClick={() => setEditing(true)} variant="ghost" size="xs">Rename</Btn>
            <Btn onClick={handleDelete} variant="danger" size="xs">Delete</Btn>
          </>
        )}
      </div>

      {/* Subtopics */}
      <ul className="mt-2 space-y-1 pl-4">
        {topic.subtopics.map(sub => (
          <SubtopicRow
            key={sub.id}
            entry={sub}
            onUpdate={onUpdate}
          />
        ))}
        <AddSubtopicRow
          chapterName={chapterName}
          topicName={topic.topic}
          syllabusType={syllabusType}
          onUpdate={onUpdate}
        />
      </ul>
    </div>
  );
}

// ── Subtopic row ───────────────────────────────────────────────────────────────

function SubtopicRow({ entry, onUpdate }: {
  entry: { id: string; subtopic: string };
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(entry.subtopic);

  const save = async () => {
    if (name.trim() === entry.subtopic || !name.trim()) { setEditing(false); return; }
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
            autoFocus value={name}
            onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") { setEditing(false); setName(entry.subtopic); } }}
            className="flex-1 rounded border border-gray-300 px-2 py-0.5 text-sm outline-none focus:ring-1 focus:ring-brand-300"
          />
          <Btn onClick={save} variant="primary" size="xs">Save</Btn>
          <Btn onClick={() => { setEditing(false); setName(entry.subtopic); }} variant="ghost" size="xs">✕</Btn>
        </>
      ) : (
        <>
          <span className="flex-1 text-sm text-gray-600">{entry.subtopic}</span>
          <Btn onClick={() => setEditing(true)} variant="ghost" size="xs">Edit</Btn>
          <Btn onClick={del} variant="danger" size="xs">✕</Btn>
        </>
      )}
    </li>
  );
}

// ── Add rows ───────────────────────────────────────────────────────────────────

function AddSubtopicRow({ chapterName, topicName, syllabusType, onUpdate }: {
  chapterName: string; topicName: string; syllabusType: SyllabusTypeKey;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const save = async () => {
    if (!val.trim()) return;
    const tree = await syllabusService.addItem(syllabusType, chapterName, topicName, val.trim());
    onUpdate(tree);
    setVal("");
    setOpen(false);
  };
  return (
    <li className="flex items-center gap-2 pt-1">
      {open ? (
        <>
          <input autoFocus value={val} onChange={e => setVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setOpen(false); }}
            placeholder="Subtopic name…"
            className="flex-1 rounded border border-gray-300 px-2 py-0.5 text-sm outline-none focus:ring-1 focus:ring-brand-300"
          />
          <Btn onClick={save} variant="primary" size="xs">Add</Btn>
          <Btn onClick={() => setOpen(false)} variant="ghost" size="xs">✕</Btn>
        </>
      ) : (
        <button onClick={() => setOpen(true)} className="text-xs text-brand-500 hover:text-brand-700 hover:underline">+ Add subtopic</button>
      )}
    </li>
  );
}

function AddTopicRow({ chapterName, syllabusType, onUpdate }: {
  chapterName: string; syllabusType: SyllabusTypeKey;
  onUpdate: (t: SyllabusTree) => void;
}) {
  const [open, setOpen] = useState(false);
  const [val, setVal] = useState("");
  const save = async () => {
    if (!val.trim()) return;
    const tree = await syllabusService.addItem(syllabusType, chapterName, val.trim());
    onUpdate(tree);
    setVal("");
    setOpen(false);
  };
  return (
    <div className="py-2">
      {open ? (
        <div className="flex items-center gap-2">
          <input autoFocus value={val} onChange={e => setVal(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setOpen(false); }}
            placeholder="Topic name…"
            className="flex-1 rounded-lg border border-gray-300 px-2 py-1 text-sm outline-none focus:ring-2 focus:ring-brand-100"
          />
          <Btn onClick={save} variant="primary" size="xs">Add</Btn>
          <Btn onClick={() => setOpen(false)} variant="ghost" size="xs">✕</Btn>
        </div>
      ) : (
        <button onClick={() => setOpen(true)} className="text-sm text-brand-500 hover:text-brand-700 hover:underline">+ Add topic</button>
      )}
    </div>
  );
}

function AddChapterRow({ syllabusType, onUpdate }: {
  syllabusType: SyllabusTypeKey; onUpdate: (t: SyllabusTree) => void;
}) {
  const [open, setOpen] = useState(false);
  const [chapter, setChapter] = useState("");
  const [topic, setTopic] = useState("");
  const save = async () => {
    if (!chapter.trim() || !topic.trim()) return;
    const tree = await syllabusService.addItem(syllabusType, chapter.trim(), topic.trim());
    onUpdate(tree);
    setChapter(""); setTopic(""); setOpen(false);
  };
  return (
    <div>
      {open ? (
        <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-brand-200 space-y-3">
          <p className="text-sm font-medium text-gray-700">New Chapter</p>
          <input value={chapter} onChange={e => setChapter(e.target.value)} placeholder="Chapter name…"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-100"
          />
          <input value={topic} onChange={e => setTopic(e.target.value)} placeholder="First topic name (required)…"
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-100"
          />
          <div className="flex gap-2">
            <Btn onClick={save} variant="primary" size="sm">Add Chapter</Btn>
            <Btn onClick={() => setOpen(false)} variant="ghost" size="sm">Cancel</Btn>
          </div>
        </div>
      ) : (
        <button onClick={() => setOpen(true)}
          className="flex w-full items-center justify-center gap-2 rounded-xl border-2 border-dashed border-gray-200 py-3 text-sm text-gray-400 hover:border-brand-300 hover:text-brand-500 transition-colors"
        >
          + Add Chapter
        </button>
      )}
    </div>
  );
}

// ── Shared button component ────────────────────────────────────────────────────

function Btn({ onClick, children, variant = "ghost", size = "sm", disabled = false }: {
  onClick: () => void; children: React.ReactNode;
  variant?: "primary" | "ghost" | "danger"; size?: "xs" | "sm"; disabled?: boolean;
}) {
  const base = "rounded font-medium transition-colors disabled:opacity-50";
  const sizes = { xs: "px-2 py-0.5 text-xs", sm: "px-3 py-1.5 text-sm" };
  const variants = {
    primary: "bg-brand-500 text-white hover:bg-brand-600",
    ghost: "text-gray-500 hover:bg-gray-100 hover:text-gray-700",
    danger: "text-red-500 hover:bg-red-50 hover:text-red-700",
  };
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${sizes[size]} ${variants[variant]}`}>
      {children}
    </button>
  );
}

// React import needed for JSX type
import React from "react";
