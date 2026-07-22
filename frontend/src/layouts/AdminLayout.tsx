import { useEffect, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  GraduationCap,
  ListTree,
  BookOpen,
  HelpCircle,
  FileText,
  Video,
  FileCheck2,
  SlidersHorizontal,
  Users,
  BarChart3,
  Settings,
  LogOut,
  ChevronsUpDown,
  Menu,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { useExam } from "../context/ExamContext";
import { BrandLogo } from "../components/BrandLogo";
import { SidebarLink, type SidebarNavItem } from "./SidebarLink";

type NavGroup = { label?: string; items: SidebarNavItem[] };

const NAV_GROUPS: NavGroup[] = [
  { items: [{ to: "/admin/dashboard", label: "Dashboard", icon: LayoutDashboard }] },
  {
    label: "Exam Setup",
    items: [
      { to: "/admin/exams", label: "Exams", icon: GraduationCap },
      { to: "/admin/syllabus", label: "Syllabus", icon: ListTree },
    ],
  },
  {
    label: "Content",
    items: [
      { to: "/admin/knowledge", label: "Knowledge", icon: BookOpen },
      { to: "/admin/mcq", label: "MCQ System", icon: HelpCircle },
      { to: "/admin/mcq-tests", label: "MCQ Tests", icon: FileText },
      { to: "/admin/subjective", label: "Subjective Tests", icon: FileCheck2 },
      { to: "/admin/video-tutor", label: "Video Tutor", icon: Video },
    ],
  },
  {
    label: "People & Insights",
    items: [
      { to: "/admin/skill-layer", label: "Skill Layer", icon: SlidersHorizontal },
      { to: "/admin/students", label: "Students", icon: Users },
      { to: "/admin/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
];

const SETTINGS_ITEM: SidebarNavItem = { to: "/admin/settings", label: "Settings", icon: Settings };

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  const initials = (user?.full_name || "A")
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <>
      {/* Logo */}
      <div className="flex h-14 flex-shrink-0 items-center border-b border-gray-200 px-4">
        <BrandLogo withName />
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-3 scrollbar-thin">
        {NAV_GROUPS.map((group, i) => (
          <div key={group.label ?? i} className={group.label ? "mt-1" : ""}>
            {group.label && (
              <div className="px-2.5 pb-1 pt-4 text-[11px] font-medium uppercase tracking-wider text-gray-400">
                {group.label}
              </div>
            )}
            <div className="space-y-0.5">
              {group.items.map((item) => (
                <SidebarLink key={item.to} item={item} onClick={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Settings + user footer */}
      <div className="border-t border-gray-200 p-3">
        <div className="mb-2">
          <SidebarLink item={SETTINGS_ITEM} onClick={onNavigate} />
        </div>
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
    </>
  );
}

export function AdminLayout() {
  const { exams, selectedExamId, setSelectedExamId } = useExam();
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  // Belt-and-braces: route change always closes the mobile drawer.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 flex-shrink-0 flex-col border-r border-gray-200 bg-white lg:flex">
        <SidebarContent />
      </aside>

      {/* Mobile drawer — inside lg:hidden so a resize past lg can never strand the scrim */}
      {drawerOpen && (
        <div className="lg:hidden">
          <div className="fixed inset-0 z-30 bg-black/30" onClick={() => setDrawerOpen(false)} aria-hidden />
          <aside className="fixed inset-y-0 left-0 z-40 flex w-72 flex-col bg-white shadow-pop">
            <SidebarContent onNavigate={() => setDrawerOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 flex-shrink-0 items-center justify-between gap-3 border-b border-gray-200 bg-white px-4 lg:justify-end lg:px-6">
          <div className="flex items-center gap-2 lg:hidden">
            <button
              onClick={() => setDrawerOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-md text-gray-600 transition-colors hover:bg-gray-100"
              aria-label="Open menu"
            >
              <Menu className="h-5 w-5" />
            </button>
            <BrandLogo size="sm" />
          </div>

          <div className="flex min-w-0 items-center gap-3">
            {/* Active exam — the universal scope for every admin workspace. */}
            <div className="relative inline-flex h-8 min-w-0 max-w-[55vw] items-center gap-2 rounded-md border border-gray-300 bg-white pl-2.5 pr-2 text-sm focus-within:border-brand-600 focus-within:ring-2 focus-within:ring-brand-600/20 lg:max-w-none">
              <GraduationCap className="h-4 w-4 flex-shrink-0 text-gray-400" />
              <select
                value={selectedExamId ?? ""}
                onChange={(e) => setSelectedExamId(e.target.value)}
                className="min-w-0 cursor-pointer appearance-none truncate bg-transparent pr-5 text-sm font-medium text-gray-700 focus:outline-none"
                title="Active exam"
              >
                {exams.length === 0 && <option value="">No exams — create one</option>}
                {exams.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name} ({e.exam_type})
                  </option>
                ))}
              </select>
              <ChevronsUpDown className="pointer-events-none absolute right-2 h-3.5 w-3.5 text-gray-400" />
            </div>
            <span className="hidden text-xs font-medium text-gray-500 sm:inline">Institute Admin</span>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-4 scrollbar-thin lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
