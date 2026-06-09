import { useCallback, useEffect, useRef, useState } from "react";
import { videoTutorService } from "../../services/videoTutor";
import type {
  AskSelectedSegment, StudentPlayerData, StudentVideoListItem,
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
    const target = Math.max(0, seconds || 0);
    const apply = () => {
      try {
        el.currentTime = target;
      } catch {
        /* element not seekable yet */
      }
      void el.play?.();
    };
    // Seeking before metadata is loaded (readyState 0) is silently clamped to 0 by
    // the browser, which is why the player jumped to the start. Wait for metadata.
    if (el.readyState >= 1) {
      apply();
    } else {
      const onReady = () => {
        el.removeEventListener("loadedmetadata", onReady);
        apply();
      };
      el.addEventListener("loadedmetadata", onReady);
      try {
        el.load();
      } catch {
        /* ignore */
      }
    }
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
        <div className="mx-auto mb-4 max-w-2xl overflow-hidden rounded-xl bg-black shadow-sm">
          {data.is_audio_only ? (
            <audio
              ref={mediaRef as React.RefObject<HTMLAudioElement>}
              src={data.media_url}
              controls
              preload="metadata"
              className="w-full"
            />
          ) : (
            <video
              ref={mediaRef as React.RefObject<HTMLVideoElement>}
              src={data.media_url}
              controls
              preload="metadata"
              className="mx-auto max-h-[45vh] w-full object-contain"
            />
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
  answer?: string;
  segments?: AskSelectedSegment[];
  followUps?: string[];
  loading: boolean;
  error?: string;
}

const STARTER_QUESTIONS = [
  "यो लेक्चरको मुख्य बुँदा के हो?",
  "यो विषय परीक्षाको लागि कसरी सोधिन्छ?",
  "अहिलेको भाग सरल भाषामा बुझाउनुहोस्।",
];

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
  const [busy, setBusy] = useState(false);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    videoTutorService
      .getHistory(videoId)
      .then((items) => {
        if (cancelled) return;
        setTurns(
          items.map((m) => ({
            question: m.question,
            answer: m.answer,
            segments: m.selected_segments,
            followUps: m.follow_up_suggestions,
            loading: false,
          })),
        );
      })
      .catch(() => {
        /* history is best-effort; a failure just starts an empty chat */
      })
      .finally(() => {
        if (!cancelled) setHistoryLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  async function ask(q: string) {
    const text = q.trim();
    if (!text || busy) return;
    setQuestion("");
    setBusy(true);
    const idx = turns.length;
    setTurns((t) => [...t, { question: text, loading: true }]);
    try {
      const res = await videoTutorService.ask(videoId, {
        question: text,
        current_video_time: getCurrentTime(),
        chat_session_id: sessionId,
      });
      setSessionId(res.chat_session_id);
      setTurns((t) =>
        t.map((turn, i) =>
          i === idx
            ? {
                ...turn,
                loading: false,
                answer: res.answer,
                segments: res.selected_segments,
                followUps: res.follow_up_suggestions,
              }
            : turn,
        ),
      );
    } catch (err) {
      setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, error: getErrorMessage(err, "उत्तर ल्याउन सकिएन।") } : turn)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <div className="min-h-[40vh] space-y-4">
        {historyLoaded && turns.length === 0 && (
          <div className="rounded-2xl bg-gradient-to-br from-brand-50 to-white p-5 text-center ring-1 ring-brand-100">
            <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-brand-600 text-lg text-white">
              ✨
            </div>
            <p className="text-sm font-semibold text-gray-800">AI Tutor लाई सोध्नुहोस्</p>
            <p className="mt-1 text-xs text-gray-500">
              लेक्चरबारे जे पनि सोध्नुहोस् — उत्तरसँगै सम्बन्धित भिडियो समय पनि देखाइन्छ।
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {STARTER_QUESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => ask(s)}
                  className="rounded-full bg-white px-3 py-1.5 text-xs text-brand-700 shadow-sm ring-1 ring-brand-100 hover:bg-brand-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn, i) => (
          <div key={i} className="space-y-2">
            {/* Student question — right aligned bubble */}
            <div className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white shadow-sm">
                {turn.question}
              </div>
            </div>

            {/* Tutor answer — left aligned */}
            <div className="flex items-start gap-2">
              <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-sm">
                🎓
              </div>
              <div className="max-w-[88%] rounded-2xl rounded-tl-sm bg-white p-4 shadow-sm ring-1 ring-gray-100">
                {turn.loading && (
                  <div className="flex items-center gap-1 py-1" aria-label="सोच्दै">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.3s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.15s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300" />
                  </div>
                )}
                {turn.error && <p className="text-sm text-red-600">{turn.error}</p>}
                {turn.answer !== undefined && (
                  <>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-gray-800">{turn.answer}</p>

                    {turn.segments && turn.segments.length > 0 && (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <p className="mb-1.5 text-xs font-medium text-gray-400">📍 भिडियोमा हेर्नुहोस्</p>
                        <div className="flex flex-wrap gap-2">
                          {turn.segments.map((s) => (
                            <button
                              key={s.segment_id}
                              onClick={() => seekTo(s.start_seconds)}
                              title={s.label}
                              className="group inline-flex items-center gap-1.5 rounded-full bg-brand-50 py-1 pl-1 pr-3 text-xs font-medium text-brand-700 ring-1 ring-brand-100 transition-colors hover:bg-brand-100"
                            >
                              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-[10px] text-white">▶</span>
                              <span className="tabular-nums">{s.start_time}</span>
                              <span className="max-w-[8rem] truncate text-brand-500 group-hover:text-brand-700">· {s.label}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}

                    {turn.followUps && turn.followUps.length > 0 && (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <p className="mb-1.5 text-xs font-medium text-gray-400">सम्भावित प्रश्न</p>
                        <div className="flex flex-wrap gap-2">
                          {turn.followUps.map((f, j) => (
                            <button
                              key={j}
                              onClick={() => ask(f)}
                              className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200"
                            >
                              {f}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); void ask(question); }}
        className="sticky bottom-16 mt-4 flex items-center gap-2 rounded-full bg-white p-1.5 shadow-md ring-1 ring-gray-200"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="प्रश्न सोध्नुहोस्…"
          className="flex-1 rounded-full border-0 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-0"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:opacity-40"
          aria-label="Send"
        >
          ➤
        </button>
      </form>
    </div>
  );
}
