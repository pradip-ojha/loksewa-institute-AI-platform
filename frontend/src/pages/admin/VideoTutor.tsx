import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { JobStatusPoller } from "../../components/JobStatusPoller";
import { videoTutorService } from "../../services/videoTutor";
import type { VideoDetail, VideoItem } from "../../services/videoTutor";
import { syllabusService, type SyllabusTree } from "../../services/syllabus";
import { getErrorMessage } from "../../utils/error";
import { RichText } from "../../components/content/RichText";

type Tab = "upload" | "library";

const TABS: { key: Tab; label: string }[] = [
  { key: "upload", label: "Upload Video/Audio" },
  { key: "library", label: "Video Library" },
];

const PROC_COLOR: Record<string, string> = {
  completed: "text-green-600",
  failed: "text-red-600",
};

export function AdminVideoTutor() {
  const [tab, setTab] = useState<Tab>("upload");
  const [detailId, setDetailId] = useState<string | null>(null);

  if (detailId) {
    return <DetailView videoId={detailId} onBack={() => setDetailId(null)} />;
  }

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Video Tutor</h1>
      <p className="mb-6 text-sm text-gray-500">
        Upload a lecture, then the system builds a transcript, timeline, summary, and AI tutor.
      </p>

      <div className="mb-6 flex gap-1 border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-brand-600 text-brand-700"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "upload" && <UploadTab onUploaded={() => setTab("library")} />}
      {tab === "library" && <LibraryTab onOpen={(id) => setDetailId(id)} />}
    </div>
  );
}

// ── Upload ──────────────────────────────────────────────────────────────────────

function UploadTab({ onUploaded }: { onUploaded: () => void }) {
  const [title, setTitle] = useState("");
  const [usageType, setUsageType] = useState("objective");
  const [topic, setTopic] = useState("");
  const [subtopic, setSubtopic] = useState("");
  const [instruction, setInstruction] = useState("");
  const mediaRef = useRef<HTMLInputElement>(null);
  const slidesRef = useRef<HTMLInputElement>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [syllabusObj, setSyllabusObj] = useState<SyllabusTree | null>(null);
  const [syllabusSubj, setSyllabusSubj] = useState<SyllabusTree | null>(null);

  useEffect(() => {
    syllabusService.getObjective().then(setSyllabusObj).catch(() => {});
    syllabusService.getSubjective().then(setSyllabusSubj).catch(() => {});
  }, []);

  const availableTopics = useMemo(() => {
    if (usageType === "objective")
      return syllabusObj?.chapters.flatMap((c) => c.topics) ?? [];
    return syllabusSubj?.chapters.flatMap((c) => c.topics) ?? [];
  }, [usageType, syllabusObj, syllabusSubj]);

  const availableSubtopics = useMemo(
    () => availableTopics.find((t) => t.topic === topic)?.subtopics ?? [],
    [topic, availableTopics]
  );

  const handleUsageChange = (value: string) => {
    setUsageType(value);
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
    const media = mediaRef.current?.files?.[0];
    if (!media) return setError("Select a video or audio file.");

    const fd = new FormData();
    fd.append("title", title);
    fd.append("content_usage_type", usageType);
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
        <p className="mb-3 text-sm text-gray-600">
          Processing your lecture. This can take several minutes depending on length.
        </p>
        <JobStatusPoller jobId={jobId} onComplete={onUploaded} />
        <button onClick={() => setJobId(null)} className="mt-4 text-sm text-brand-700">
          Upload another
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-4 rounded-xl bg-white p-6 shadow-sm">
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Lecture title *</label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          placeholder="e.g. मौलिक हक — परिचय"
        />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Syllabus</label>
        <select
          value={usageType}
          onChange={(e) => handleUsageChange(e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="objective">Objective</option>
          <option value="subjective">Subjective</option>
        </select>
        <p className="mt-1 text-xs text-gray-400">
          Decides which syllabus topics and knowledge notes the tutor uses.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Topic (optional)</label>
          <select
            value={topic}
            onChange={(e) => handleTopicChange(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
          >
            <option value="">— Leave blank for auto-detection —</option>
            {availableTopics.map((t) => (
              <option key={t.topic} value={t.topic}>{t.topic}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">Subtopic (optional)</label>
          {availableSubtopics.length > 0 ? (
            <select
              value={subtopic}
              onChange={(e) => setSubtopic(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm"
            >
              <option value="">— Leave blank for auto-detection —</option>
              {availableSubtopics.map((s) => (
                <option key={s.id} value={s.subtopic}>{s.subtopic}</option>
              ))}
            </select>
          ) : (
            <select value="" disabled className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm">
              <option value="">{topic ? "No subtopics for this topic" : "Select a topic first"}</option>
            </select>
          )}
        </div>
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Custom instruction (optional)</label>
        <textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} rows={2} className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm" />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Video / audio file *</label>
        <input ref={mediaRef} type="file" accept="video/*,audio/*" className="text-sm" />
      </div>

      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">Support slides PDF (optional)</label>
        <input ref={slidesRef} type="file" accept="application/pdf" className="text-sm" />
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}
      <button type="submit" disabled={submitting} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
        {submitting ? "Uploading…" : "Upload & Process"}
      </button>
    </form>
  );
}

// ── Library ─────────────────────────────────────────────────────────────────────

function LibraryTab({ onOpen }: { onOpen: (id: string) => void }) {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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

  async function remove(v: VideoItem) {
    if (!confirm(`Delete "${v.display_name}"?`)) return;
    try {
      await videoTutorService.deleteVideo(v.id);
      void load();
    } catch (err) {
      setError(getErrorMessage(err, "Delete failed."));
    }
  }

  if (loading) return <p className="text-sm text-gray-500">Loading…</p>;
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (videos.length === 0) return <p className="text-sm text-gray-500">No videos uploaded yet.</p>;

  return (
    <div className="space-y-3">
      {videos.map((v) => (
        <div key={v.id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h3 className="truncate font-semibold text-gray-800">{v.display_name}</h3>
              <p className="text-xs text-gray-400">
                {v.content_usage_type}
                {v.topic ? ` · ${v.topic}` : ""}
                {v.is_audio_only ? " · audio" : " · video"}
                {v.has_slides ? " · slides" : ""}
              </p>
              <p className="mt-1 text-xs">
                <span className={PROC_COLOR[v.processing_status] ?? "text-blue-600"}>
                  {v.processing_status}
                </span>
                {v.status === "active" && <span className="ml-2 text-green-600">● active</span>}
              </p>
            </div>
            <div className="flex flex-shrink-0 flex-wrap justify-end gap-2">
              {v.processing_status === "completed" && (
                <>
                  <button onClick={() => onOpen(v.id)} className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm text-gray-700">
                    View
                  </button>
                  <button onClick={() => activate(v)} className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white">
                    {v.status === "active" ? "Deactivate" : "Activate"}
                  </button>
                </>
              )}
              {v.processing_status === "failed" && (
                <button onClick={() => retry(v)} className="rounded-lg bg-yellow-500 px-3 py-1.5 text-sm font-medium text-white">
                  Retry
                </button>
              )}
              <button onClick={() => remove(v)} className="rounded-lg px-3 py-1.5 text-sm text-red-600 hover:bg-red-50">
                Delete
              </button>
            </div>
          </div>
        </div>
      ))}
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

  if (error) return <div><button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button><p className="text-sm text-red-600">{error}</p></div>;
  if (!v) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div className="max-w-3xl">
      <button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back to library</button>
      <h1 className="text-2xl font-bold text-gray-900">{v.display_name}</h1>
      <p className="mb-4 text-sm text-gray-500">
        {v.content_usage_type}{v.topic ? ` · ${v.topic}` : ""} · {v.status}
      </p>

      {v.media_url && (
        <div className="mb-6 overflow-hidden rounded-xl bg-black">
          {v.is_audio_only ? (
            <audio src={v.media_url} controls className="w-full" />
          ) : (
            <video src={v.media_url} controls className="max-h-[420px] w-full" />
          )}
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
            {v.summary.key_points.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </Section>
      ) : null}
      {v.summary?.exam_focused_points?.length ? (
        <Section title="Exam-Focused Points">
          <ul className="list-disc pl-5 text-sm text-gray-700">
            {v.summary.exam_focused_points.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </Section>
      ) : null}

      <Section title={`Timeline (${v.timeline.length})`}>
        <div className="space-y-2">
          {v.timeline.map((s) => (
            <div key={s.segment_id} className="rounded-lg border border-gray-100 p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-gray-800">{s.label}</span>
                <span className="text-xs text-gray-400">{s.start_time}–{s.end_time}</span>
              </div>
              <p className="mt-1 text-xs text-gray-500">{s.description}</p>
              {s.topic && <p className="mt-1 text-xs text-brand-700">{s.topic}{s.subtopic_ids?.length ? ` › ${s.subtopic_ids.join(", ")}` : ""}</p>}
            </div>
          ))}
        </div>
      </Section>

      {v.slides.length > 0 && (
        <Section title={`Slides (${v.slides.length})`}>
          <div className="space-y-2">
            {v.slides.map((s) => (
              <div key={s.slide_id} className="rounded-lg border border-gray-100 p-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-gray-800">#{s.slide_number} {s.title}</span>
                  <span className="text-xs text-gray-400">{s.related_timestamps.join(", ")}</span>
                </div>
                {s.summary && <p className="mt-1 text-xs text-gray-500">{s.summary}</p>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {(v.summary?.possible_questions?.mcqs?.length || v.summary?.possible_questions?.short?.length) ? (
        <Section title="Possible Questions">
          {v.summary?.possible_questions?.mcqs?.map((q, i) => (
            <div key={`m${i}`} className="mb-2 text-sm text-gray-700">
              <p className="font-medium">{i + 1}. {q.question}</p>
              <ul className="ml-4 text-xs text-gray-500">
                {q.options.map((o, j) => <li key={j}>{o}</li>)}
              </ul>
              <p className="text-xs text-green-700">Answer: {q.answer}</p>
            </div>
          ))}
          {v.summary?.possible_questions?.short?.map((q, i) => (
            <p key={`s${i}`} className="text-sm text-gray-700">• {q}</p>
          ))}
          {v.summary?.possible_questions?.long?.map((q, i) => (
            <p key={`l${i}`} className="text-sm text-gray-700">◆ {q}</p>
          ))}
        </Section>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-5 rounded-xl bg-white p-5 shadow-sm">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500">{title}</h2>
      {children}
    </div>
  );
}
