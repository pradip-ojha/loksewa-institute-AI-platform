import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft, Play, Sparkles, FileText, ListVideo, Lightbulb, GraduationCap,
  Send, Video as VideoIcon, Headphones, BookOpen, Target, Notebook,
} from "lucide-react";
import { videoTutorService } from "../../services/videoTutor";
import type {
  AskSelectedSegment, StudentPlayerData, StudentVideoListItem,
} from "../../services/videoTutor";
import { useStudentExam } from "../../context/StudentExamContext";
import { getErrorMessage } from "../../utils/error";
import { PageHeader, Card, Badge, EmptyState, Skeleton, Alert } from "../../components/ui";
import { RichText } from "../../components/content/RichText";
import {
  KeyPointsList, ExamPointCallout, TermsGlossary, PossibleQuestionsCard, ContentSectionTitle,
} from "../../components/content/LearningContent";

type PlayerTab = "summary" | "timeline" | "keypoints" | "practice" | "tutor";

const TABS: { key: PlayerTab; label: string; icon: React.ReactNode }[] = [
  { key: "summary", label: "सारांश", icon: <FileText className="h-4 w-4" /> },
  { key: "timeline", label: "समयरेखा", icon: <ListVideo className="h-4 w-4" /> },
  { key: "keypoints", label: "मुख्य बुँदा", icon: <Lightbulb className="h-4 w-4" /> },
  { key: "practice", label: "अभ्यास", icon: <Target className="h-4 w-4" /> },
  { key: "tutor", label: "AI Tutor", icon: <Sparkles className="h-4 w-4" /> },
];

export function StudentVideoTutor() {
  const { selectedExamId } = useStudentExam();
  const [videos, setVideos] = useState<StudentVideoListItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setVideos(await videoTutorService.listStudentVideos(selectedExamId));
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load videos."));
    } finally {
      setLoading(false);
    }
  }, [selectedExamId]);

  useEffect(() => {
    if (!activeId) void load();
  }, [activeId, load]);

  if (activeId) {
    return <PlayerView videoId={activeId} onBack={() => setActiveId(null)} />;
  }

  return (
    <div className="pb-20">
      <PageHeader
        title="Video Tutor"
        description="रेकर्ड गरिएका लेक्चरहरू हेर्नुहोस् र AI सँग सोध्नुहोस्"
        icon={<VideoIcon className="h-5 w-5" />}
      />
      {error && <Alert className="mb-3">{error}</Alert>}
      {loading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 w-full rounded-2xl" />)}
        </div>
      ) : videos.length === 0 ? (
        <EmptyState
          icon={<VideoIcon className="h-6 w-6" />}
          title="अहिले कुनै लेक्चर उपलब्ध छैन"
          description="नयाँ लेक्चर थपिएपछि यहाँ देखिनेछ।"
        />
      ) : (
        <div className="space-y-3">
          {videos.map((v) => (
            <Card
              key={v.id}
              interactive
              padded={false}
              onClick={() => setActiveId(v.id)}
              className="flex items-center gap-4 p-4"
            >
              <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                {v.is_audio_only ? <Headphones className="h-5 w-5" /> : <Play className="h-5 w-5" />}
              </div>
              <div className="min-w-0 flex-1">
                <h3 className="truncate font-semibold text-gray-800">{v.display_name}</h3>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge tone="neutral">{v.is_audio_only ? "Audio" : "Video"}</Badge>
                  {v.topic && <Badge tone="brand">{v.topic}</Badge>}
                </div>
              </div>
            </Card>
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

  const backBtn = (
    <button onClick={onBack} className="mb-3 inline-flex items-center gap-1 text-sm font-medium text-brand-700 hover:text-brand-800">
      <ArrowLeft className="h-4 w-4" /> Back
    </button>
  );

  if (error)
    return <div className="pb-20">{backBtn}<Alert>{error}</Alert></div>;
  if (!data)
    return (
      <div className="pb-20">
        {backBtn}
        <Skeleton className="mb-4 aspect-video w-full max-w-2xl rounded-2xl" />
        <Skeleton className="h-40 w-full rounded-2xl" />
      </div>
    );

  const hasSummary = !!data.summary;

  return (
    <div className="pb-24">
      {backBtn}
      <h1 className="mb-3 text-lg font-bold text-gray-900 font-deva">{data.display_name}</h1>

      {data.media_url && (
        <div className="mx-auto mb-4 max-w-2xl overflow-hidden rounded-2xl bg-black shadow-card">
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

      <div className="mb-4 flex gap-1 overflow-x-auto border-b border-gray-200 scrollbar-thin">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2.5 text-sm font-medium transition-colors ${
              tab === t.key
                ? "border-brand-600 text-brand-700"
                : "border-transparent text-gray-500 hover:text-gray-800"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {tab === "summary" && (
        <div className="space-y-3">
          {!hasSummary && (
            <EmptyState icon={<FileText className="h-6 w-6" />} title="सारांश तयार हुँदैछ" />
          )}
          {data.summary?.short_summary && (
            <Card className="border-l-4 border-l-brand-500 bg-brand-50/40">
              <ContentSectionTitle icon={<Sparkles className="h-4 w-4" />}>द्रुत सारांश</ContentSectionTitle>
              <p className="font-deva text-sm leading-relaxed text-gray-700">{data.summary.short_summary}</p>
            </Card>
          )}
          {data.summary?.detailed_summary && (
            <Card>
              <ContentSectionTitle icon={<BookOpen className="h-4 w-4" />}>विस्तृत सारांश</ContentSectionTitle>
              <RichText>{data.summary.detailed_summary}</RichText>
            </Card>
          )}
        </div>
      )}

      {tab === "timeline" && (
        <div className="space-y-2">
          {data.timeline.length === 0 && (
            <EmptyState icon={<ListVideo className="h-6 w-6" />} title="समयरेखा उपलब्ध छैन" />
          )}
          {data.timeline.map((s, i) => (
            <Card key={s.segment_id} padded={false} className="overflow-hidden p-4">
              <div className="flex items-start gap-3">
                <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-xs font-bold text-brand-700">
                  {i + 1}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-gray-800 font-deva">{s.label}</span>
                    <Badge tone="neutral" className="tabular-nums">{s.start_time}–{s.end_time}</Badge>
                  </div>
                  {s.description && <p className="mt-1 text-xs text-gray-500 font-deva">{s.description}</p>}
                  {s.summary && <p className="mt-1.5 text-sm text-gray-700 font-deva">{s.summary}</p>}
                  <button
                    onClick={() => seekTo(s.start_seconds)}
                    className="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-brand-50 px-3 py-1.5 text-xs font-semibold text-brand-700 transition-colors hover:bg-brand-100"
                  >
                    <Play className="h-3 w-3" /> Video मा जानुहोस्
                  </button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {tab === "keypoints" && (
        <div className="space-y-3">
          {data.summary?.key_points?.length ? (
            <Card>
              <ContentSectionTitle icon={<Lightbulb className="h-4 w-4" />}>मुख्य बुँदा</ContentSectionTitle>
              <KeyPointsList items={data.summary.key_points} />
            </Card>
          ) : null}
          {data.summary?.exam_focused_points?.length ? (
            <ExamPointCallout items={data.summary.exam_focused_points} />
          ) : null}
          {data.summary?.important_terms?.length ? (
            <Card>
              <ContentSectionTitle icon={<Notebook className="h-4 w-4" />}>महत्त्वपूर्ण शब्दहरू</ContentSectionTitle>
              <TermsGlossary items={data.summary.important_terms} />
            </Card>
          ) : null}
          {!data.summary?.key_points?.length &&
            !data.summary?.exam_focused_points?.length &&
            !data.summary?.important_terms?.length && (
              <EmptyState icon={<Lightbulb className="h-6 w-6" />} title="मुख्य बुँदा उपलब्ध छैन" />
            )}
        </div>
      )}

      {tab === "practice" && (
        <div className="space-y-3">
          {data.summary?.possible_questions ? (
            <Card>
              <ContentSectionTitle icon={<Target className="h-4 w-4" />}>अभ्यास प्रश्नहरू</ContentSectionTitle>
              <PossibleQuestionsCard data={data.summary.possible_questions} />
            </Card>
          ) : (
            <EmptyState icon={<Target className="h-6 w-6" />} title="अभ्यास प्रश्न उपलब्ध छैन" />
          )}
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
  // In-flight stream, aborted when the tab/player unmounts so a backgrounded answer
  // stops generating (saves Azure spend) and never setStates an unmounted component.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

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
    const controller = new AbortController();
    abortRef.current = controller;
    let streamed = false;
    let failedMsg: string | null = null;
    try {
    await videoTutorService.askStream(
      videoId,
      { question: text, current_video_time: getCurrentTime(), chat_session_id: sessionId },
      {
        onMeta: (meta) => {
          setSessionId(meta.chat_session_id);
          const segs = (meta.selected_segments as AskSelectedSegment[] | undefined) ?? [];
          setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, segments: segs } : turn)));
        },
        onDelta: (delta) => {
          streamed = true;
          setTurns((t) =>
            t.map((turn, i) =>
              i === idx ? { ...turn, loading: false, answer: (turn.answer ?? "") + delta } : turn,
            ),
          );
        },
        onDone: (done) => {
          setTurns((t) =>
            t.map((turn, i) =>
              i === idx
                ? { ...turn, loading: false, answer: turn.answer ?? "", followUps: done.follow_up_suggestions ?? [] }
                : turn,
            ),
          );
        },
        onError: (message) => {
          failedMsg = message;
        },
      },
      controller.signal,
    );
    // Stream failed before any answer text arrived → retry once via the plain
    // (non-stream) endpoint before surfacing the error.
    if (failedMsg !== null) {
      if (!streamed && !controller.signal.aborted) {
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
                    segments: res.selected_segments ?? [],
                    followUps: res.follow_up_suggestions ?? [],
                  }
                : turn,
            ),
          );
          failedMsg = null;
        } catch {
          /* fall through to the stream's error message */
        }
      }
      if (failedMsg !== null) {
        const message = failedMsg;
        setTurns((t) => t.map((turn, i) => (i === idx ? { ...turn, loading: false, error: message } : turn)));
      }
    }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <div className="min-h-[40vh] space-y-4">
        {historyLoaded && turns.length === 0 && (
          <div className="rounded-lg border border-gray-200 bg-white p-5 text-center">
            <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-brand-50 text-brand-600">
              <Sparkles className="h-5 w-5" />
            </div>
            <p className="text-sm font-semibold text-gray-800">AI Tutor लाई सोध्नुहोस्</p>
            <p className="mt-1 text-xs text-gray-500 font-deva">
              लेक्चरबारे जे पनि सोध्नुहोस् — उत्तरसँगै सम्बन्धित भिडियो समय पनि देखाइन्छ।
            </p>
            <div className="mt-3 flex flex-wrap justify-center gap-2">
              {STARTER_QUESTIONS.map((s, i) => (
                <button
                  key={i}
                  onClick={() => ask(s)}
                  className="rounded-full bg-white px-3 py-1.5 text-xs text-brand-700 shadow-sm ring-1 ring-brand-100 transition-colors hover:bg-brand-50 font-deva"
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
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white shadow-sm font-deva">
                {turn.question}
              </div>
            </div>

            {/* Tutor answer — left aligned */}
            <div className="flex items-start gap-2">
              <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
                <GraduationCap className="h-4 w-4" />
              </div>
              <div className="max-w-[88%] rounded-2xl rounded-tl-sm bg-white p-4 shadow-sm ring-1 ring-gray-100">
                {turn.loading && (
                  <div className="flex items-center gap-1 py-1" aria-label="सोच्दै">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.3s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.15s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300" />
                  </div>
                )}
                {turn.error && <p className="text-sm text-danger-600">{turn.error}</p>}
                {turn.answer !== undefined && (
                  <>
                    <RichText size="sm">{turn.answer}</RichText>

                    {turn.segments && turn.segments.length > 0 && (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <p className="mb-1.5 text-xs font-medium text-gray-400">भिडियोमा हेर्नुहोस्</p>
                        <div className="flex flex-wrap gap-2">
                          {turn.segments.map((s) => (
                            <button
                              key={s.segment_id}
                              onClick={() => seekTo(s.start_seconds)}
                              title={s.label}
                              className="group inline-flex items-center gap-1.5 rounded-full bg-brand-50 py-1 pl-1 pr-3 text-xs font-medium text-brand-700 ring-1 ring-brand-100 transition-colors hover:bg-brand-100"
                            >
                              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-600 text-white"><Play className="h-2.5 w-2.5" /></span>
                              <span className="tabular-nums">{s.start_time}</span>
                              <span className="max-w-[8rem] truncate text-brand-500 group-hover:text-brand-700 font-deva">· {s.label}</span>
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
                              className="rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200 font-deva"
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
          className="flex-1 rounded-full border-0 bg-transparent px-3 py-2 text-sm focus:outline-none focus:ring-0 font-deva"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:opacity-40"
          aria-label="Send"
        >
          <Send className="h-4 w-4" />
        </button>
      </form>
    </div>
  );
}
