import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { HelpCircle, Video, FileCheck2, BarChart3, Sparkles, ChevronRight } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const QUICK_ACTIONS = [
  { label: "Start MCQ Test", to: "/student/mcq-tests", icon: HelpCircle, grad: "from-brand-500 to-brand-700" },
  { label: "Watch Lecture", to: "/student/video-tutor", icon: Video, grad: "from-accent-500 to-accent-700" },
  { label: "Upload Answer Sheet", to: "/student/subjective-tests", icon: FileCheck2, grad: "from-success-500 to-success-700" },
  { label: "View Results", to: "/student/results", icon: BarChart3, grad: "from-warning-500 to-warning-600" },
];

export function StudentDashboard() {
  const { user } = useAuth();
  const firstName = (user?.full_name || "").split(" ")[0] || "there";

  return (
    <div className="pb-20">
      {/* Gradient hero */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className="relative mb-6 overflow-hidden rounded-2xl bg-gradient-to-br from-brand-600 to-accent-600 p-6 text-white shadow-card"
      >
        <div className="absolute -right-6 -top-6 h-28 w-28 rounded-full bg-white/10" />
        <div className="relative">
          <p className="flex items-center gap-1.5 text-sm font-medium text-white/80">
            <Sparkles className="h-4 w-4" /> नमस्ते
          </p>
          <h2 className="mt-1 text-2xl font-bold">Hello, {firstName} 👋</h2>
          <p className="mt-1 text-sm text-white/80">आज के सिक्ने? Pick where you want to start.</p>
        </div>
      </motion.div>

      {/* Quick actions */}
      <div className="grid grid-cols-2 gap-3">
        {QUICK_ACTIONS.map((action, i) => {
          const Icon = action.icon;
          return (
            <motion.div
              key={action.to}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, delay: i * 0.05 }}
            >
              <Link
                to={action.to}
                className="group flex h-full flex-col justify-between rounded-2xl bg-white p-4 shadow-card ring-1 ring-gray-100 transition-shadow hover:shadow-card-hover"
              >
                <div className={`mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br ${action.grad} text-white shadow-sm`}>
                  <Icon className="h-5 w-5" />
                </div>
                <span className="flex items-center justify-between text-sm font-semibold text-gray-800">
                  {action.label}
                  <ChevronRight className="h-4 w-4 text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-brand-500" />
                </span>
              </Link>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
