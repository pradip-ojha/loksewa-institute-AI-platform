import { useEffect, useState } from "react";
import api from "../../services/api";

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
  created_at: string;
}

const CARD_DEFS = [
  { key: "total_students", label: "Total Students", color: "text-blue-700", bg: "bg-blue-50" },
  { key: "total_knowledge_documents", label: "Knowledge Documents", color: "text-purple-700", bg: "bg-purple-50" },
  { key: "total_approved_mcqs", label: "Approved MCQs", color: "text-yellow-700", bg: "bg-yellow-50" },
  { key: "total_active_mcq_sets", label: "Active MCQ Sets", color: "text-orange-700", bg: "bg-orange-50" },
  { key: "total_subjective_tests", label: "Subjective Tests", color: "text-pink-700", bg: "bg-pink-50" },
  { key: "total_videos", label: "Video Lectures", color: "text-teal-700", bg: "bg-teal-50" },
  { key: "pending_jobs", label: "Pending Jobs", color: "text-gray-700", bg: "bg-gray-50" },
  { key: "failed_jobs", label: "Failed Jobs", color: "text-red-700", bg: "bg-red-50" },
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
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-gray-900">Dashboard</h2>
        <p className="mt-1 text-sm text-gray-500">Platform overview</p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {CARD_DEFS.map(card => (
          <div key={card.key} className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
            <p className="text-xs font-medium text-gray-500">{card.label}</p>
            <p className={`mt-2 text-2xl font-bold ${card.color}`}>
              {loading ? "—" : stats ? String(stats[card.key]) : "—"}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-8 rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <h3 className="mb-4 text-sm font-semibold text-gray-700">Recent Activity</h3>
        {activity.length === 0 ? (
          <p className="text-sm text-gray-400">Activity feed available after content is added.</p>
        ) : (
          <ul className="divide-y divide-gray-100">
            {activity.map((item, i) => (
              <li key={i} className="flex items-center justify-between py-2">
                <div>
                  <span className="inline-block rounded-full bg-gray-100 px-2 py-0.5 text-xs font-medium text-gray-600 mr-2">
                    {TYPE_LABELS[item.type] || item.type}
                  </span>
                  <span className="text-sm text-gray-700">{item.title}</span>
                </div>
                <span className="text-xs text-gray-400">
                  {new Date(item.created_at).toLocaleDateString()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
