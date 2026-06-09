import { NavLink, Outlet } from "react-router-dom";
import { motion } from "framer-motion";
import { Home, HelpCircle, Video, FileCheck2, BarChart3, User } from "lucide-react";

const NAV_ITEMS = [
  { to: "/student/dashboard", label: "Home", icon: Home },
  { to: "/student/mcq-tests", label: "MCQ", icon: HelpCircle },
  { to: "/student/video-tutor", label: "Videos", icon: Video },
  { to: "/student/subjective-tests", label: "Subjective", icon: FileCheck2 },
  { to: "/student/results", label: "Results", icon: BarChart3 },
  { to: "/student/profile", label: "Profile", icon: User },
];

export function StudentLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-gray-50 pb-[calc(4rem+env(safe-area-inset-bottom))]">
      {/* Top header */}
      <header className="sticky top-0 z-10 flex h-14 items-center border-b border-gray-200 bg-white/90 px-4 backdrop-blur-sm">
        <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 shadow-glow">
          <span className="text-sm font-bold text-white">N</span>
        </div>
        <span className="ml-2 font-bold text-gray-900">NeuraFix AI</span>
      </header>

      <main className="flex-1 px-4 py-4">
        <Outlet />
      </main>

      {/* Bottom navigation bar */}
      <nav className="fixed bottom-0 left-0 right-0 z-10 flex h-16 border-t border-gray-200 bg-white/95 pb-[env(safe-area-inset-bottom)] backdrop-blur-sm">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `relative flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors ${
                  isActive ? "text-brand-600" : "text-gray-400 hover:text-gray-600"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="student-tab-indicator"
                      className="absolute top-0 h-0.5 w-8 rounded-full bg-brand-500"
                    />
                  )}
                  <Icon className="h-[20px] w-[20px]" strokeWidth={isActive ? 2.4 : 2} />
                  <span className="truncate">{item.label}</span>
                </>
              )}
            </NavLink>
          );
        })}
      </nav>
    </div>
  );
}
