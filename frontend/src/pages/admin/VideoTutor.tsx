import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Video, ArrowLeft } from "lucide-react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import { videoTutorService } from "../../services/videoTutor";
import type { VideoDetail, VideoItem } from "../../services/videoTutor";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";
import { getErrorMessage } from "../../utils/error";
import { RichText } from "../../components/content/RichText";
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
  type Column,
} from "../../components/ui";

type Tab = "upload" | "library";

const TABS = [
  { id: "upload", label: "Upload Video/Audio" },
  { id: "library", label: "Video Library" },
];

export function AdminVideoTutor() {
  const [tab, setTab] = useState<Tab>("upload");
  const [detailId, setDetailId] = useState<string | null>(null);

  if (detailId) {
    return <DetailView videoId={detailId} onBack={() => setDetailId(null)} />;
  }

  return (
    <div>
      <PageHeader
        title="Video Tutor"
        description="Upload a lecture, then the system builds a transcript, timeline, summary, and AI tutor."
        icon={<Video className="h-5 w-5" />}
      />

      <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} className="mb-6" />

      {tab === "upload" && <UploadTab onUploaded={() => setTab("library")} />}
      {tab === "library" && <LibraryTab onOpen={(id) => setDetailId(id)} />}
    </div>
  );
}

// ── Upload ──────────────────────────────────────────────────────────────────────

function UploadTab({ onUploaded }: { onUploaded: () => void }) {
  const { selectedExamId, selectedExam } = useExam();
  const [title, setTitle] = useState("");
  const [chapter, setChapter] = useState("");
  const [topic, setTopic] = useState("");
  const [subtopic, setSubtopic] = useState("");
  const [instruction, setInstruction] = useState("");
  const mediaRef = useRef<HTMLInputElement>(null);
  const slidesRef = useRef<HTMLInputElement>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [tree, setTree] = useState<SyllabusTree | null>(null);

  useEffect(() => {
    if (!selectedExamId) {
      setTree(null);
      return;
    }
    syllabusService.get(selectedExamId).then(setTree).catch(() => setTree(null));
  }, [selectedExamId]);

  const availableChapters = useMemo(() => tree?.chapters ?? [], [tree]);

  const availableTopics = useMemo(
    () => (chapter ? availableChapters.find((c) => c.chapter === chapter)?.topics ?? [] : availableChapters.flatMap((c) => c.topics)),
    [chapter, availableChapters],
  );

  const availableSubtopics = useMemo(() => availableTopics.find((t) => t.topic === topic)?.subtopics ?? [], [topic, availableTopics]);

  const handleChapterChange = (value: string) => {
    setChapter(value);
    setTopic("");
    setSubtopic("");
  };

  const handleTopicChange = (value: string) => {
    setTopic(value);
    setSubtopic("");
  };

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (!title.trim()) return setError("Title is required.");
    if (!selectedExamId) return setError("Select an exam in the top bar first.");
    const media = mediaRef.current?.files?.[0];
    if (!media) return setError("Select a video or audio file.");

    const fd = new FormData();
    fd.append("title", title);
    fd.append("exam_id", selectedExamId);
    if (chapter.trim()) fd.append("chapter", chapter.trim());
    if (topic.trim()) fd.append("topic", topic.trim());
    if (subtopic.trim()) fd.append("subtopic", subtopic.trim());
    if (instruction.trim()) fd.append("custom_instruction", instruction.trim());
    fd.append("media", media);
    const slides = slidesRef.current?.files?.[0];
    if (slides) fd.append("support_slides", slides);

    setSubmitting(true);
    try {
      const job = await videoTutorService.uploadVideo(fd);
      setJobId(job.id);
    } catch (err) {
      setError(getErrorMessage(err, "Upload failed."));
    } finally {
      setSubmitting(false);
    }
  }

  if (jobId) {
    return (
      <div className="max-w-xl">
        <p className="mb-3 text-sm text-gray-600">Processing your lecture. This can take several minutes depending on length.</p>
        <JobStatusPoller jobId={jobId} onComplete={onUploaded} />
        <Button variant="ghost" className="mt-4" onClick={() => setJobId(null)}>
          Upload another
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4 rounded-lg border border-gray-200 bg-white p-6">
      <FormField label="Lecture title" required>
        <TextInput value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. मौलिक हक — परिचय" />
      </FormField>

      <FormField label="Exam" hint="Decides which syllabus topics and knowledge notes the tutor uses.">
        <div className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
          {selectedExam ? `${selectedExam.name} (${selectedExam.exam_type})` : "Select an exam in the top bar"}
        </div>
      </FormField>

      <FormField label="Chapter (optional)" hint="The primary dimension for fetching notes — topics narrow within it.">
        <Select value={chapter} onChange={(e) => handleChapterChange(e.target.value)}>
          <option value="">— Leave blank for auto-detection —</option>
          {availableChapters.map((c) => (
            <option key={c.chapter} value={c.chapter}>
              {c.chapter}
            </option>
          ))}
        </Select>
      </FormField>

      <div className="grid grid-cols-2 gap-3">
        <FormField label="Topic (optional)">
          <Select value={topic} onChange={(e) => handleTopicChange(e.target.value)}>
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
            <Select value={subtopic} onChange={(e) => setSubtopic(e.target.value)}>
              <option value="">— Leave blank for auto-detection —</option>
              {availableSubtopics.map((s) => (
                <option key={s.id} value={s.subtopic}>
                  {s.subtopic}
                </option>
              ))}
            </Select>
          ) : (
            <Select value="" disabled>
              <option value="">{topic ? "No subtopics for this topic" : "Select a topic first"}</option>
            </Select>
          )}
        </FormField>
      </div>

      <FormField label="Custom instruction (optional)">
        <Textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={2} />
      </FormField>

      <FormField label="Video / audio file" required>
        <input ref={mediaRef} type="file" accept="video/*,audio/*" className="text-sm" />
      </FormField>

      <FormField label="Support slides PDF (optional)">
        <input ref={slidesRef} type="file" accept="application/pdf" className="text-sm" />
      </FormField>

      {error && <Alert>{error}</Alert>}
      <Button type="submit" loading={submitting}>
        Upload &amp; Process
      </Button>
    </form>
  );
}

// ── Library ─────────────────────────────────────────────────────────────────────

function LibraryTab({ onOpen }: { onOpen: (id: string) => void }) {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [confirmDel, setConfirmDel] = useState<VideoItem | null>(null);
  const [delBusy, setDelBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await videoTutorService.listVideos(1, 100);
      setVideos(res.items);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load videos."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh while anything is still processing.
  useEffect(() => {
    const anyProcessing = videos.some((v) => !["completed", "failed"].includes(v.processing_status));
    if (!anyProcessing) return;
    const id = setInterval(() => void load(), 4000);
    return () => clearInterval(id);
  }, [videos, load]);

  async function activate(v: VideoItem) {
    try {
      await (v.status === "active" ? videoTutorService.deactivate(v.id) : videoTutorService.activate(v.id));
      void load();
    } catch (err) {
      setError(getErrorMessage(err, "Action failed."));
    }
  }

  async function retry(v: VideoItem) {
    try {
      await videoTutorService.retryVideo(v.id);
      void load();
    } catch (err) {
      setError(getErrorMessage(err, "Retry failed."));
    }
  }

  async function confirmRemove() {
    if (!confirmDel) return;
    setDelBusy(true);
    try {
      await videoTutorService.deleteVideo(confirmDel.id);
      setConfirmDel(null);
      void load();
    } catch (err) {
      setError(getErrorMessage(err, "Delete failed."));
    } finally {
      setDelBusy(false);
    }
  }

  const columns: Column<VideoItem>[] = [
    {
      key: "display_name",
      header: "Lecture",
      accessor: (v) => v.display_name,
      render: (v) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-gray-800">{v.display_name}</div>
          <div className="truncate text-xs text-gray-400">
            {v.chapter || "Unscoped"}
            {v.topic ? ` · ${v.topic}` : ""}
            {v.is_audio_only ? " · audio" : " · video"}
            {v.has_slides ? " · slides" : ""}
          </div>
        </div>
      ),
    },
    {
      key: "processing_status",
      header: "Processing",
      render: (v) => <StatusBadge status={v.processing_status} />,
    },
    {
      key: "status",
      header: "State",
      render: (v) => <StatusBadge status={v.status} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "8rem",
      render: (v) => (
        <div className="flex items-center justify-end gap-2">
          {v.processing_status === "completed" && (
            <>
              <Button variant="ghost" size="xs" onClick={() => onOpen(v.id)}>
                View
              </Button>
              <Button size="xs" onClick={() => activate(v)}>
                {v.status === "active" ? "Deactivate" : "Activate"}
              </Button>
            </>
          )}
          {v.processing_status === "failed" && (
            <Button variant="secondary" size="xs" onClick={() => retry(v)}>
              Retry
            </Button>
          )}
          <Menu items={[{ label: "Delete", tone: "danger", onClick: () => setConfirmDel(v) }]} />
        </div>
      ),
    },
  ];

  if (loading) return <PageLoader />;

  return (
    <div>
      {error && <Alert className="mb-3">{error}</Alert>}
      <DataTable
        columns={columns}
        rows={videos}
        rowKey={(v) => v.id}
        emptyState={<EmptyState icon={<Video className="h-5 w-5" />} title="No videos uploaded yet" className="border-0" />}
      />
      <ConfirmDialog
        open={!!confirmDel}
        title="Delete video"
        description={confirmDel ? `Delete "${confirmDel.display_name}"? This cannot be undone.` : ""}
        confirmLabel="Delete"
        tone="danger"
        loading={delBusy}
        onConfirm={confirmRemove}
        onClose={() => setConfirmDel(null)}
      />
    </div>
  );
}

// ── Detail ──────────────────────────────────────────────────────────────────────

function DetailView({ videoId, onBack }: { videoId: string; onBack: () => void }) {
  const [v, setV] = useState<VideoDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    videoTutorService
      .getVideo(videoId)
      .then(setV)
      .catch((err) => setError(getErrorMessage(err, "Failed to load video.")));
  }, [videoId]);

  const backBtn = (
    <Button variant="ghost" size="sm" icon={<ArrowLeft className="h-4 w-4" />} onClick={onBack}>
      Back to library
    </Button>
  );

  if (error)
    return (
      <div>
        <div className="mb-3">{backBtn}</div>
        <Alert>{error}</Alert>
      </div>
    );
  if (!v) return <PageLoader />;

  return (
    <div className="max-w-3xl">
      <div className="mb-3">{backBtn}</div>
      <h1 className="text-lg font-semibold tracking-tight text-gray-900">{v.display_name}</h1>
      <p className="mb-4 text-sm text-gray-500">
        {v.chapter || "Unscoped"}
        {v.topic ? ` · ${v.topic}` : ""} · {v.status}
      </p>

      {v.media_url && (
        <div className="mb-6 overflow-hidden rounded-lg bg-black">
          {v.is_audio_only ? <audio src={v.media_url} controls className="w-full" /> : <video src={v.media_url} controls className="max-h-[420px] w-full" />}
        </div>
      )}

      {v.summary && (
        <Section title="Short Summary">
          <p className="text-sm text-gray-700">{v.summary.short_summary}</p>
        </Section>
      )}
      {v.summary?.detailed_summary && (
        <Section title="Detailed Summary">
          <RichText>{v.summary.detailed_summary}</RichText>
        </Section>
      )}
      {v.summary?.key_points?.length ? (
        <Section title="Key Points">
          <ul className="list-disc pl-5 text-sm text-gray-700">
            {v.summary.key_points.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Section>
      ) : null}
      {v.summary?.exam_focused_points?.length ? (
        <Section title="Exam-Focused Points">
          <ul className="list-disc pl-5 text-sm text-gray-700">
            {v.summary.exam_focused_points.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title={`Timeline (${v.timeline.length})`}>
        <div className="space-y-2">
          {v.timeline.map((s) => (
            <div key={s.segment_id} className="rounded-md border border-gray-200 p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-gray-800">{s.label}</span>
                <span className="text-xs tabular-nums text-gray-400">
                  {s.start_time}–{s.end_time}
                </span>
              </div>
              <p className="mt-1 text-xs text-gray-500">{s.description}</p>
              {s.topic && (
                <p className="mt-1 text-xs text-brand-700">
                  {s.topic}
                  {s.subtopic_ids?.length ? ` › ${s.subtopic_ids.join(", ")}` : ""}
                </p>
              )}
            </div>
          ))}
        </div>
      </Section>

      {v.slides.length > 0 && (
        <Section title={`Slides (${v.slides.length})`}>
          <div className="space-y-2">
            {v.slides.map((s) => (
              <div key={s.slide_id} className="rounded-md border border-gray-200 p-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-gray-800">
                    #{s.slide_number} {s.title}
                  </span>
                  <span className="text-xs text-gray-400">{s.related_timestamps.join(", ")}</span>
                </div>
                {s.summary && <p className="mt-1 text-xs text-gray-500">{s.summary}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {v.summary?.possible_questions?.mcqs?.length || v.summary?.possible_questions?.short?.length ? (
        <Section title="Possible Questions">
          {v.summary?.possible_questions?.mcqs?.map((q, i) => (
            <div key={`m${i}`} className="mb-2 text-sm text-gray-700">
              <p className="font-medium">
                {i + 1}. {q.question}
              </p>
              <ul className="ml-4 text-xs text-gray-500">
                {q.options.map((o, j) => (
                  <li key={j}>{o}</li>
                ))}
              </ul>
              <p className="text-xs text-success-700">Answer: {q.answer}</p>
            </div>
          ))}
          {v.summary?.possible_questions?.short?.map((q, i) => (
            <p key={`s${i}`} className="text-sm text-gray-700">
              • {q}
            </p>
          ))}
          {v.summary?.possible_questions?.long?.map((q, i) => (
            <p key={`l${i}`} className="text-sm text-gray-700">
              ◆ {q}
            </p>
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 rounded-lg border border-gray-200 bg-white p-5">
      <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-400">{title}</h2>
      {children}
    </div>
  );
}
