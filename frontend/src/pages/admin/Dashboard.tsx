import { useEffect, useState } from "react";
import {
  LayoutDashboard, Users, BookOpen, HelpCircle, FileText, FileCheck2, Video,
  Clock, AlertTriangle, Activity,
} from "lucide-react";
import api from "../../services/api";
import { PageHeader, Card, StatCard, EmptyState, Skeleton, Badge } from "../../components/ui";

interface Stats {
  total_students: number;
  total_active_students: number;
  total_knowledge_documents: number;
  total_approved_mcqs: number;
  total_active_mcq_sets: number;
  total_subjective_tests: number;
  total_videos: number;
  pending_jobs: number;
  failed_jobs: number;
}

interface ActivityItem {
  type: string;
  title: string;
  status?: string;
  created_at: string;
}

const CARD_DEFS = [
  { key: "total_students", label: "Total Students", icon: Users, tone: "brand" as const },
  { key: "total_knowledge_documents", label: "Knowledge Docs", icon: BookOpen, tone: "accent" as const },
  { key: "total_approved_mcqs", label: "Approved MCQs", icon: HelpCircle, tone: "info" as const },
  { key: "total_active_mcq_sets", label: "Active MCQ Sets", icon: FileText, tone: "success" as const },
  { key: "total_subjective_tests", label: "Subjective Tests", icon: FileCheck2, tone: "warning" as const },
  { key: "total_videos", label: "Video Lectures", icon: Video, tone: "accent" as const },
  { key: "pending_jobs", label: "Pending Jobs", icon: Clock, tone: "neutral" as const },
  { key: "failed_jobs", label: "Failed Jobs", icon: AlertTriangle, tone: "danger" as const },
] as const;

const TYPE_LABELS: Record<string, string> = {
  mcq_upload: "MCQ Upload",
  mcq_generation: "MCQ Generation",
  answer_submission: "Answer Submitted",
  video_question: "Video Question",
  skill_update: "Skill Updated",
  knowledge_upload: "Knowledge Upload",
};

export function AdminDashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [activity, setActivity] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/api/admin/dashboard/stats")
      .then(r => {
        setStats(r.data);
        setActivity(r.data.recent_activity || []);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <PageHeader title="Dashboard" description="Platform overview at a glance" icon={<LayoutDashboard className="h-5 w-5" />} />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {loading
          ? CARD_DEFS.map((_, i) => <Skeleton key={i} className="h-28 rounded-2xl" />)
          : CARD_DEFS.map((card, i) => (
              <StatCard
                key={card.key}
                index={i}
                tone={card.tone}
                label={card.label}
                value={stats ? String(stats[card.key]) : "—"}
                icon={<card.icon className="h-5 w-5" />}
                hint={card.key === "total_students" && stats ? `${stats.total_active_students} active` : undefined}
              />
            ))}
      </div>

      <Card className="mt-8">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-800">
          <Activity className="h-4 w-4 text-brand-500" /> Recent Activity
        </h3>
        {activity.length === 0 ? (
          <EmptyState icon={<Activity className="h-6 w-6" />} title="No activity yet" description="The feed populates as content is added." />
        ) : (
          <ul className="divide-y divide-gray-100">
            {activity.map((item, i) => (
              <li key={i} className="flex items-center justify-between gap-3 py-2.5">
                <div className="flex min-w-0 items-center gap-2.5">
                  <Badge tone="neutral">{TYPE_LABELS[item.type] || item.type}</Badge>
                  <span className="truncate text-sm text-gray-700 font-deva">{item.title}</span>
                </div>
                <span className="flex-shrink-0 text-xs text-gray-400">
                  {new Date(item.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
