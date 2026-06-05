import { useCallback, useEffect, useRef, useState } from "react";
import { videoTutorService } from "../../services/videoTutor";
import type {
  AskResponse, StudentPlayerData, StudentVideoListItem,
} from "../../services/videoTutor";
import { getErrorMessage } from "../../utils/error";

type PlayerTab = "summary" | "timeline" | "keypoints" | "tutor";

const TABS: { key: PlayerTab; label: string }[] = [
  { key: "summary", label: "सारांश" },
  { key: "timeline", label: "समयरेखा" },
  { key: "keypoints", label: "मुख्य बुँदा" },
  { key: "tutor", label: "AI Tutor" },
];

export function StudentVideoTutor() {
  const [videos, setVideos] = useState<StudentVideoListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setVideos(await videoTutorService.listStudentVideos());
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load videos."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!activeId) void load();
  }, [activeId, load]);

  if (activeId) {
    return <PlayerView videoId={activeId} onBack={() => setActiveId(null)} />;
  }

  return (
    <div className="pb-20">
      <h1 className="mb-4 text-xl font-bold text-gray-900">Video Tutor</h1>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {loading ? (
        <p className="text-sm text-gray-500">Loading…</p>
      ) : videos.length === 0 ? (
        <p className="text-sm text-gray-500">No lectures available right now.</p>
      ) : (
        <div className="space-y-3">
          {videos.map((v) => (
            <button
              key={v.id}
              onClick={() => setActiveId(v.id)}
              className="block w-full rounded-xl bg-white p-4 text-left shadow-sm ring-1 ring-gray-100"
            >
              <h3 className="font-semibold text-gray-800">{v.display_name}</h3>
              <p className="text-xs text-gray-400">
                {v.is_audio_only ? "Audio" : "Video"}{v.topic ? ` · ${v.topic}` : ""}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Player ──────────────────────────────────────────────────────────────────────

function PlayerView({ videoId, onBack }: { videoId: string; onBack: () => void }) {
  const [data, setData] = useState<StudentPlayerData | null>(null);
  const [tab, setTab] = useState<PlayerTab>("summary");
  const [error, setError] = useState("");
  const mediaRef = useRef<HTMLVideoElement & HTMLAudioElement>(null);

  useEffect(() => {
    videoTutorService
      .getPlayerData(videoId)
      .then(setData)
      .catch((err) => setError(getErrorMessage(err, "Failed to load lecture.")));
  }, [videoId]);

  const seekTo = useCallback((seconds: number) => {
    const el = mediaRef.current;
    if (!el) return;
    el.currentTime = seconds;
    void el.play?.();
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  function currentTime(): string | null {
    const el = mediaRef.current;
    if (!el || !Number.isFinite(el.currentTime)) return null;
    const s = Math.floor(el.currentTime);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  if (error) return <div className="pb-20"><button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button><p className="text-sm text-red-600">{error}</p></div>;
  if (!data) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div className="pb-24">
      <button onClick={onBack} className="mb-3 text-sm text-brand-700">← Back</button>
      <h1 className="mb-3 text-lg font-bold text-gray-900">{data.display_name}</h1>

      {data.media_url && (
        <div className="mb-4 overflow-hidden rounded-xl bg-black">
          {data.is_audio_only ? (
            <audio ref={mediaRef as React.RefObject<HTMLAudioElement>} src={data.media_url} controls className="w-full" />
          ) : (
            <video ref={mediaRef as React.RefObject<HTMLVideoElement>} src={data.media_url} controls className="w-full" />
          )}
        </div>
      )}

      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-gray-200">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`whitespace-nowrap px-3 py-2 text-sm font-medium ${
              tab === t.key ? "border-b-2 border-brand-600 text-brand-700" : "text-gray-500"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "summary" && (
        <div className="space-y-3">
          {data.summary?.short_summary && (
            <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <p className="text-sm text-gray-700">{data.summary.short_summary}</p>
            </div>
          )}
          {data.summary?.detailed_summary && (
            <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <p className="whitespace-pre-wrap text-sm text-gray-700">{data.summary.detailed_summary}</p>
            </div>
          )}
        </div>
      )}

      {tab === "timeline" && (
        <div className="space-y-2">
          {data.timeline.map((s) => (
            <div key={s.segment_id} className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <div className="flex items-center justify-between">
                <span className="font-medium text-gray-800">{s.label}</span>
                <span className="text-xs text-gray-400">{s.start_time}–{s.end_time}</span>
              </div>
              {s.description && <p className="mt-1 text-xs text-gray-500">{s.description}</p>}
              {s.summary && <p className="mt-1 text-sm text-gray-700">{s.summary}</p>}
              <button
                onClick={() => seekTo(s.start_seconds)}
                className="mt-2 rounded-lg bg-brand-50 px-3 py-1 text-xs font-medium text-brand-700"
              >
                ▶ Video मा जानुहोस्
              </button>
            </div>
          ))}
        </div>
      )}

      {tab === "keypoints" && (
        <div className="space-y-3">
          {data.summary?.key_points?.length ? (
            <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">मुख्य बुँदा</h3>
              <ul className="list-disc pl-5 text-sm text-gray-700">
                {data.summary.key_points.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          ) : null}
          {data.summary?.exam_focused_points?.length ? (
            <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">Exam Focus</h3>
              <ul className="list-disc pl-5 text-sm text-gray-700">
                {data.summary.exam_focused_points.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          ) : null}
          {data.summary?.important_terms?.length ? (
            <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
              <h3 className="mb-2 text-sm font-semibold text-gray-700">Important Terms</h3>
              <ul className="list-disc pl-5 text-sm text-gray-700">
                {data.summary.important_terms.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {tab === "tutor" && (
        <TutorTab videoId={videoId} getCurrentTime={currentTime} seekTo={seekTo} />
      )}
    </div>
  );
}

// ── Tutor Q&A ─────────────────────────────────────────────────────────────────────

interface ChatTurn {
  question: string;
  response?: AskResponse;
  loading: boolean;
  error?: string;
}

function TutorTab({
  videoId, getCurrentTime, seekTo,
}: {
  videoId: string;
  getCurrentTime: () => string | null;
  seekTo: (s: number) => void;
}) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);

  async function ask(q: string) {
    const text = q.trim();
    if (!text) return;
    setQuestion("");
    const idx = turns.length;
    setTurns((t) => [...t, { question: text, loading: true }]);
    try {
      const res = await videoTutorService.ask(videoId, {
        question: text,
        current_video_time: getCurrentTime(),
        chat_session_id: sessionId,
      });
      setSessionId(res.chat_session_id);
      setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, response: res } : turn)));
    } catch (err) {
      setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, error: getErrorMessage(err, "Failed to get an answer.") } : turn)));
    }
  }

  return (
    <div>
      <div className="space-y-4">
        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            <div className="rounded-xl bg-brand-50 p-3 text-sm text-brand-900">{turn.question}</div>
            {turn.loading && <p className="text-sm text-gray-500">सोच्दै…</p>}
            {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
            {turn.response && (
              <div className="rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
                <p className="whitespace-pre-wrap text-sm text-gray-800">{turn.response.answer}</p>

                {turn.response.selected_segments.length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {turn.response.selected_segments.map((s) => (
                      <button
                        key={s.segment_id}
                        onClick={() => seekTo(s.start_seconds)}
                        className="rounded-lg bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-700"
                      >
                        ▶ {s.start_time} · {s.label}
                      </button>
                    ))}
                  </div>
                )}

                {turn.response.follow_up_suggestions.length > 0 && (
                  <div className="mt-3 border-t border-gray-100 pt-2">
                    <p className="mb-1 text-xs text-gray-400">सम्भावित प्रश्न:</p>
                    <div className="flex flex-wrap gap-2">
                      {turn.response.follow_up_suggestions.map((f, j) => (
                        <button
                          key={j}
                          onClick={() => ask(f)}
                          className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700"
                        >
                          {f}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); void ask(question); }}
        className="mt-4 flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="प्रश्न सोध्नुहोस्…"
          className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
        <button type="submit" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white">
          Send
        </button>
      </form>
    </div>
  );
}
