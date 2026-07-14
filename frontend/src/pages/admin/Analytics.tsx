import { useEffect, useState } from "react";
import { BarChart3 } from "lucide-react";
import { analyticsService } from "../../services/analytics";
import type {
  MCQOverview,
  SubjectiveOverview,
  SubjectiveTestDetail,
  VideoOverviewItem,
  VideoDetail,
} from "../../services/analytics";
import { PageHeader, Tabs, StatCard, Card as UICard, CardHeader } from "../../components/ui";

type Tab = "mcq" | "subjective" | "video";

const TABS = [
  { id: "mcq", label: "MCQ Analytics" },
  { id: "subjective", label: "Subjective Analytics" },
  { id: "video", label: "Video Tutor Analytics" },
];

// ── Small shared UI (delegates to the shared kit) ─────────────────────────────
function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <UICard>
      <CardHeader title={title} />
      {children}
    </UICard>
  );
}

function AccuracyBar({ value }: { value: number }) {
  const color = value >= 75 ? "bg-success-500" : value >= 50 ? "bg-warning-500" : "bg-danger-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full ${color}`} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
      <span className="w-10 text-right text-xs tabular-nums text-gray-600">{value}%</span>
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-gray-400">{text}</p>;
}

// ── MCQ ───────────────────────────────────────────────────────────────────────
export function MCQAnalyticsView() {
  const [data, setData] = useState<MCQOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    analyticsService.mcqOverview().then(setData).catch(() => {}).finally(() => setLoading(false));
  }, []);

  if (loading) return <Empty text="Loading…" />;
  if (!data) return <Empty text="Could not load MCQ analytics." />;
  const s = data.summary;
  if (s.total_attempts === 0) return <Empty text="No submitted MCQ attempts yet." />;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Total Attempts" value={s.total_attempts} />
        <StatCard label="Students" value={s.total_students} />
        <StatCard label="Average Score" value={`${s.average_score_percent}%`} />
        <StatCard label="Highest" value={`${s.highest_score_percent}%`} tone="success" />
        <StatCard label="Lowest" value={`${s.lowest_score_percent}%`} tone="danger" />
      </div>

      <Card title="Topic-wise Performance">
        {data.topic_performance.length === 0 ? <Empty text="No data." /> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-gray-500">
              <th className="pb-2">Topic</th><th className="pb-2">Answered</th><th className="pb-2">Correct</th><th className="pb-2">Accuracy</th>
            </tr></thead>
            <tbody className="divide-y divide-gray-100">
              {data.topic_performance.map((t, i) => (
                <tr key={i}>
                  <td className="py-2 pr-3 text-gray-800">{t.topic}</td>
                  <td className="py-2 text-gray-600">{t.total}</td>
                  <td className="py-2 text-gray-600">{t.correct}</td>
                  <td className="py-2"><AccuracyBar value={t.accuracy} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Weak Subtopics (< 60%)">
        {data.weak_subtopics.length === 0 ? <Empty text="No weak subtopics — nice." /> : (
          <ul className="space-y-2">
            {data.weak_subtopics.map((t, i) => (
              <li key={i} className="flex items-center justify-between">
                <span className="text-sm text-gray-700">{t.subtopic} <span className="text-xs text-gray-400">({t.topic})</span></span>
                <AccuracyBar value={t.accuracy} />
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Student Results">
          {data.student_results.length === 0 ? <Empty text="No students yet." /> : (
            <table className="w-full text-sm">
              <thead><tr className="text-left text-xs text-gray-500">
                <th className="pb-2">Student</th><th className="pb-2">Attempts</th><th className="pb-2">Avg</th><th className="pb-2">Best</th>
              </tr></thead>
              <tbody className="divide-y divide-gray-100">
                {data.student_results.map((r) => (
                  <tr key={r.student_id}>
                    <td className="py-2 pr-3">
                      <div className="text-gray-800">{r.student_name}</div>
                      <div className="text-xs text-gray-400">{r.student_email}</div>
                    </td>
                    <td className="py-2 text-gray-600">{r.attempts}</td>
                    <td className="py-2 text-gray-600">{r.average_percent}%</td>
                    <td className="py-2 text-gray-600">{r.best_percent}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Hardest Questions (lowest correct %)">
          {data.hardest_questions.length === 0 ? <Empty text="No data." /> : (
            <ul className="space-y-3">
              {data.hardest_questions.map((q) => (
                <li key={q.question_id}>
                  <div className="flex items-start justify-between gap-3">
                    <span className="text-sm text-gray-700">{q.question_text}</span>
                    <span className="whitespace-nowrap text-xs font-medium tabular-nums text-danger-600">{q.accuracy}%</span>
                  </div>
                  <div className="text-xs text-gray-400">{q.topic} · answered {q.times_answered}×</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}

// ── Subjective ────────────────────────────────────────────────────────────────
export function SubjectiveAnalyticsView() {
  const [data, setData] = useState<SubjectiveOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<SubjectiveTestDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    analyticsService.subjectiveOverview().then(setData).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const openDetail = (testId: string) => {
    setDetailLoading(true);
    setDetail(null);
    analyticsService.subjectiveTestDetail(testId).then(setDetail).catch(() => {}).finally(() => setDetailLoading(false));
  };

  if (loading) return <Empty text="Loading…" />;
  if (!data) return <Empty text="Could not load subjective analytics." />;
  const s = data.summary;
  if (s.total_submissions === 0) return <Empty text="No answer-sheet submissions yet." />;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard label="Submissions" value={s.total_submissions} />
        <StatCard label="Checked" value={s.checked_submissions} />
        <StatCard label="Students" value={s.total_students} />
        <StatCard label="Avg Score" value={`${s.average_percent}%`} />
        <StatCard label="Low Confidence" value={s.low_confidence_count} tone="warning" />
        <StatCard label="Checked PDFs" value={s.checked_pdf_count} />
      </div>

      <Card title="Per-Test Breakdown">
        {data.test_breakdown.length === 0 ? <Empty text="No checked submissions yet." /> : (
          <table className="w-full text-sm">
            <thead><tr className="text-left text-xs text-gray-500">
              <th className="pb-2">Test</th><th className="pb-2">Submissions</th><th className="pb-2">Avg Marks</th>
              <th className="pb-2">High</th><th className="pb-2">Low</th><th className="pb-2">Avg %</th><th></th>
            </tr></thead>
            <tbody className="divide-y divide-gray-100">
              {data.test_breakdown.map((t) => (
                <tr key={t.test_id}>
                  <td className="py-2 pr-3 text-gray-800">{t.display_name}</td>
                  <td className="py-2 text-gray-600">{t.submissions}</td>
                  <td className="py-2 text-gray-600">{t.average_marks}{t.total_marks ? ` / ${t.total_marks}` : ""}</td>
                  <td className="py-2 text-gray-600">{t.highest_marks}</td>
                  <td className="py-2 text-gray-600">{t.lowest_marks}</td>
                  <td className="py-2"><AccuracyBar value={t.average_percent} /></td>
                  <td className="py-2 text-right">
                    <button onClick={() => openDetail(t.test_id)} className="text-xs font-medium text-brand-600 hover:text-brand-700">View</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="Common Mistakes (most-missed points)">
        {data.common_mistakes.length === 0 ? <Empty text="No recurring mistakes recorded." /> : (
          <ul className="space-y-2">
            {data.common_mistakes.map((m, i) => (
              <li key={i} className="flex items-center justify-between">
                <span className="text-sm text-gray-700">{m.text}</span>
                <span className="rounded-full bg-warning-50 px-2 py-0.5 text-xs font-medium tabular-nums text-warning-700">{m.count}×</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {(detailLoading || detail) && (
        <Card title={detail ? `Test Detail — ${detail.display_name}` : "Test Detail"}>
          {detailLoading ? <Empty text="Loading…" /> : detail && (
            <div className="space-y-5">
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Question-wise Average</h4>
                {detail.question_performance.length === 0 ? <Empty text="No data." /> : (
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-xs text-gray-500">
                      <th className="pb-2">Question</th><th className="pb-2">Submissions</th><th className="pb-2">Avg Awarded</th><th className="pb-2">Accuracy</th>
                    </tr></thead>
                    <tbody className="divide-y divide-gray-100">
                      {detail.question_performance.map((q, i) => (
                        <tr key={i}>
                          <td className="py-2 pr-3 text-gray-800">{q.question_number}</td>
                          <td className="py-2 text-gray-600">{q.submissions}</td>
                          <td className="py-2 text-gray-600">{q.average_awarded} / {q.average_max}</td>
                          <td className="py-2"><AccuracyBar value={q.accuracy} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Student Marks</h4>
                {detail.student_results.length === 0 ? <Empty text="No data." /> : (
                  <table className="w-full text-sm">
                    <thead><tr className="text-left text-xs text-gray-500">
                      <th className="pb-2">Student</th><th className="pb-2">Marks</th><th className="pb-2">%</th>
                    </tr></thead>
                    <tbody className="divide-y divide-gray-100">
                      {detail.student_results.map((r) => (
                        <tr key={r.student_id}>
                          <td className="py-2 pr-3">
                            <div className="text-gray-800">{r.student_name}</div>
                            <div className="text-xs text-gray-400">{r.student_email}</div>
                          </td>
                          <td className="py-2 text-gray-600">{r.marks_awarded} / {r.marks_possible}</td>
                          <td className="py-2 text-gray-600">{r.percent}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

// ── Video ─────────────────────────────────────────────────────────────────────
export function VideoAnalyticsView() {
  const [rows, setRows] = useState<VideoOverviewItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<VideoDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    analyticsService.videoOverview().then(setRows).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const openDetail = (videoId: string) => {
    setDetailLoading(true);
    setDetail(null);
    analyticsService.videoDetail(videoId).then(setDetail).catch(() => {}).finally(() => setDetailLoading(false));
  };

  if (loading) return <Empty text="Loading…" />;
  if (!rows) return <Empty text="Could not load video analytics." />;
  if (rows.length === 0) return <Empty text="No videos uploaded yet." />;

  return (
    <div className="space-y-5">
      <Card title="Videos">
        <table className="w-full text-sm">
          <thead><tr className="text-left text-xs text-gray-500">
            <th className="pb-2">Video</th><th className="pb-2">Views</th><th className="pb-2">Viewers</th>
            <th className="pb-2">Questions</th><th className="pb-2">Low Conf.</th><th></th>
          </tr></thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((v) => (
              <tr key={v.video_id}>
                <td className="py-2 pr-3 text-gray-800">{v.display_name}</td>
                <td className="py-2 text-gray-600">{v.total_views}</td>
                <td className="py-2 text-gray-600">{v.unique_viewers}</td>
                <td className="py-2 text-gray-600">{v.total_questions}</td>
                <td className="py-2 text-gray-600">{v.low_confidence_answers}</td>
                <td className="py-2 text-right">
                  <button onClick={() => openDetail(v.video_id)} className="text-xs font-medium text-brand-600 hover:text-brand-700">View</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {(detailLoading || detail) && (
        <Card title={detail ? `Video Detail — ${detail.display_name}` : "Video Detail"}>
          {detailLoading ? <Empty text="Loading…" /> : detail && (
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatCard label="Views" value={detail.total_views} />
                <StatCard label="Unique Viewers" value={detail.unique_viewers} />
                <StatCard label="Questions" value={detail.total_questions} />
                <StatCard label="Low-Confidence" value={detail.low_confidence_answers.length} tone="warning" />
              </div>

              <div className="grid gap-5 lg:grid-cols-2">
                <div>
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Most Asked Questions</h4>
                  {detail.most_asked_questions.length === 0 ? <Empty text="No questions yet." /> : (
                    <ul className="space-y-2">
                      {detail.most_asked_questions.map((q, i) => (
                        <li key={i} className="flex items-center justify-between gap-3">
                          <span className="text-sm text-gray-700">{q.question}</span>
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium tabular-nums text-gray-600">{q.count}×</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Unclear Concepts</h4>
                  {detail.unclear_concepts.length === 0 ? <Empty text="None flagged." /> : (
                    <ul className="space-y-2">
                      {detail.unclear_concepts.map((c, i) => (
                        <li key={i} className="flex items-center justify-between">
                          <span className="text-sm text-gray-700">{c.topic}</span>
                          <span className="rounded-full bg-warning-50 px-2 py-0.5 text-xs font-medium tabular-nums text-warning-700">{c.count}×</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>

              <div>
                <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Questions per Student</h4>
                {detail.student_questions.length === 0 ? <Empty text="No data." /> : (
                  <table className="w-full text-sm">
                    <tbody className="divide-y divide-gray-100">
                      {detail.student_questions.map((r) => (
                        <tr key={r.student_id}>
                          <td className="py-2 pr-3">
                            <div className="text-gray-800">{r.student_name}</div>
                            <div className="text-xs text-gray-400">{r.student_email}</div>
                          </td>
                          <td className="py-2 text-right text-gray-600">{r.questions}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>

              {detail.low_confidence_answers.length > 0 && (
                <div>
                  <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">Low-Confidence Answers</h4>
                  <ul className="space-y-2">
                    {detail.low_confidence_answers.map((a, i) => (
                      <li key={i} className="flex items-start justify-between gap-3">
                        <span className="text-sm text-gray-700">{a.question}</span>
                        <span className="whitespace-nowrap text-xs font-medium tabular-nums text-warning-600">{Math.round(a.confidence * 100)}%</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────
export function AdminAnalytics() {
  const [tab, setTab] = useState<Tab>("mcq");

  return (
    <div>
      <PageHeader
        title="Analytics"
        description="Performance across MCQ tests, answer-sheet checking, and the video tutor."
        icon={<BarChart3 className="h-5 w-5" />}
      />

      <Tabs items={TABS} value={tab} onChange={(id) => setTab(id as Tab)} className="mb-5" />

      {tab === "mcq" && <MCQAnalyticsView />}
      {tab === "subjective" && <SubjectiveAnalyticsView />}
      {tab === "video" && <VideoAnalyticsView />}
    </div>
  );
}
