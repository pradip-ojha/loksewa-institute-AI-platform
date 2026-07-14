import { Link } from "react-router-dom";
import { HelpCircle, Video, FileCheck2, BarChart3, Sparkles, ChevronRight } from "lucide-react";
import { useAuth } from "../../context/AuthContext";

const QUICK_ACTIONS = [
  { label: "MCQ Tests", desc: "अभ्यास परीक्षा", to: "/student/mcq-tests", icon: HelpCircle },
  { label: "Video Lectures", desc: "भिडियो कक्षा", to: "/student/video-tutor", icon: Video },
  { label: "Subjective Tests", desc: "उत्तरपुस्तिका जाँच", to: "/student/subjective-tests", icon: FileCheck2 },
  { label: "AI Tutor", desc: "प्रश्न सोध्नुहोस्", to: "/student/tutor", icon: Sparkles },
  { label: "Results", desc: "नतिजा हेर्नुहोस्", to: "/student/results", icon: BarChart3 },
];

export function StudentDashboard() {
  const { user } = useAuth();
  const firstName = (user?.full_name || "").split(" ")[0] || "there";

  return (
    <div className="pb-20">
      <div className="mb-5">
        <h2 className="font-deva text-lg font-semibold text-gray-900">नमस्ते, {firstName}</h2>
        <p className="mt-0.5 text-sm text-gray-500">आज के सिक्ने? Pick where you want to start.</p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {QUICK_ACTIONS.map((action) => {
          const Icon = action.icon;
          return (
            <Link
              key={action.to}
              to={action.to}
              className="group flex h-full flex-col justify-between rounded-lg border border-gray-200 bg-white p-4 transition-colors hover:border-gray-300 hover:bg-gray-50"
            >
              <div className="mb-3 flex h-9 w-9 items-center justify-center rounded-md bg-brand-50 text-brand-600">
                <Icon className="h-5 w-5" />
              </div>
              <div>
                <div className="flex items-center justify-between text-sm font-medium text-gray-900">
                  {action.label}
                  <ChevronRight className="h-4 w-4 text-gray-300 transition-transform group-hover:translate-x-0.5 group-hover:text-brand-600" />
                </div>
                <p className="mt-0.5 font-deva text-xs text-gray-500">{action.desc}</p>
              </div>
            </Link>
          );
        })}
      </div>
    </div>
  );
}
