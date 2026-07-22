import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import {
  Home,
  HelpCircle,
  Video,
  Sparkles,
  User,
  GraduationCap,
  ChevronDown,
  FileCheck2,
  LogOut,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useStudentExam } from "../context/StudentExamContext";
import { BrandLogo } from "../components/BrandLogo";
import { SidebarLink, type SidebarNavItem } from "./SidebarLink";

const NAV_ITEMS: SidebarNavItem[] = [
  { to: "/student/dashboard", label: "Home", icon: Home },
  { to: "/student/mcq-tests", label: "MCQ", icon: HelpCircle },
  { to: "/student/video-tutor", label: "Videos", icon: Video },
  { to: "/student/tutor", label: "AI Tutor", icon: Sparkles },
  { to: "/student/profile", label: "Profile", icon: User },
];

// Desktop sidebar has room for the full nav (mobile keeps 5 tabs; Subjective
// stays reachable there via Dashboard/Profile).
const SIDEBAR_ITEMS: SidebarNavItem[] = [
  NAV_ITEMS[0],
  NAV_ITEMS[1],
  { to: "/student/subjective-tests", label: "Subjective Tests", icon: FileCheck2 },
  NAV_ITEMS[2],
  NAV_ITEMS[3],
  NAV_ITEMS[4],
];

function ExamSelector() {
  const { exams, selectedExamId, setSelectedExamId, isLoading } = useStudentExam();

  if (isLoading) return null;
  if (exams.length === 0) return null;

  return (
    <div className="relative inline-flex h-9 min-w-0 flex-1 items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 pl-2.5 pr-7 text-brand-800 shadow-sm focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-500/20">
      <GraduationCap className="h-4 w-4 flex-shrink-0 text-brand-600" />
      <select
        value={selectedExamId ?? ""}
        onChange={(e) => setSelectedExamId(e.target.value)}
        className="w-full cursor-pointer appearance-none truncate bg-transparent text-xs font-semibold text-brand-800 focus:outline-none"
        title="Selected exam — applies to MCQ, Video, Subjective and AI Tutor"
        aria-label="Selected exam"
      >
        {exams.map((e) => (
          <option key={e.exam_id} value={e.exam_id}>{e.name}</option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 flex-shrink-0 text-brand-500" />
    </div>
  );
}

export function StudentLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  const initials = (user?.full_name || "S")
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Desktop sidebar — hidden below lg */}
      <aside className="hidden lg:fixed lg:inset-y-0 lg:left-0 lg:z-20 lg:flex lg:w-64 lg:flex-col lg:border-r lg:border-gray-200 lg:bg-white">
        <div className="flex h-14 flex-shrink-0 items-center border-b border-gray-200 px-4">
          <BrandLogo withName />
        </div>
        <div className="flex px-3 pt-3">
          <ExamSelector />
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-3 scrollbar-thin">
          {SIDEBAR_ITEMS.map((item) => (
            <SidebarLink key={item.to} item={item} />
          ))}
        </nav>
        <div className="border-t border-gray-200 p-3">
          <div className="flex items-center gap-2.5 rounded-md px-2 py-1.5">
            <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700">
              {initials}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-gray-800">{user?.full_name}</div>
              <div className="truncate text-xs text-gray-400">{user?.email}</div>
            </div>
            <button
              onClick={handleLogout}
              className="rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-danger-600"
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-h-screen flex-col pb-[calc(4rem+env(safe-area-inset-bottom))] lg:pb-0 lg:pl-64">
        {/* Mobile top header */}
        <header className="sticky top-0 z-10 flex h-14 items-center justify-between gap-2 border-b border-gray-200 bg-white px-3 lg:hidden">
          <div className="flex flex-shrink-0 items-center gap-2">
            <BrandLogo />
          </div>

          <ExamSelector />

          <NavLink
            to="/student/profile"
            className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700"
            aria-label="Profile"
          >
            {initials}
          </NavLink>
        </header>

        <main className="flex-1 px-4 py-4 lg:px-8 lg:py-6">
          <div className="mx-auto w-full max-w-5xl">
            <Outlet />
          </div>
        </main>
      </div>

      {/* Bottom navigation bar — mobile only */}
      <nav className="fixed bottom-0 left-0 right-0 z-10 flex h-16 border-t border-gray-200 bg-white pb-[env(safe-area-inset-bottom)] lg:hidden">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `relative flex flex-1 flex-col items-center justify-center gap-0.5 text-[11px] font-medium transition-colors ${
                  isActive ? "text-brand-700" : "text-gray-400 hover:text-gray-600"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="student-tab-indicator"
                      className="absolute top-0 h-0.5 w-6 rounded-full bg-brand-600"
                    />
                  )}
                  <Icon className="h-5 w-5" strokeWidth={isActive ? 2.4 : 2} />
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
