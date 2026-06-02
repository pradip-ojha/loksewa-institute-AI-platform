import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

const QUICK_ACTIONS = [
  { label: "Start MCQ Test", to: "/student/mcq-tests", icon: "❓", color: "bg-blue-500" },
  { label: "Watch Video", to: "/student/video-tutor", icon: "🎥", color: "bg-purple-500" },
  { label: "Upload Answer Sheet", to: "/student/subjective-tests", icon: "📄", color: "bg-green-500" },
  { label: "View Results", to: "/student/results", icon: "📊", color: "bg-orange-500" },
];

export function StudentDashboard() {
  const { user } = useAuth();

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-lg font-semibold text-gray-900">Hello, {user?.full_name} 👋</h2>
        <p className="mt-0.5 text-sm text-gray-500">What would you like to do today?</p>
      </div>

      {/* Quick actions */}
      <div className="mb-6 grid grid-cols-2 gap-3">
        {QUICK_ACTIONS.map((action) => (
          <Link
            key={action.to}
            to={action.to}
            className="flex flex-col items-center rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 transition hover:shadow-md"
          >
            <div className={`mb-2 flex h-12 w-12 items-center justify-center rounded-full ${action.color}`}>
              <span className="text-2xl">{action.icon}</span>
            </div>
            <span className="text-center text-xs font-medium text-gray-700">{action.label}</span>
          </Link>
        ))}
      </div>

      {/* Summary cards */}
      <div className="space-y-3">
        {[
          { label: "Active MCQ Tests", value: "—" },
          { label: "Active Subjective Tests", value: "—" },
          { label: "Available Videos", value: "—" },
        ].map((card) => (
          <div key={card.label} className="flex items-center justify-between rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100">
            <span className="text-sm text-gray-600">{card.label}</span>
            <span className="font-semibold text-gray-900">{card.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
