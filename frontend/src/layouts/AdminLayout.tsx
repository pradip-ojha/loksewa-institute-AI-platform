import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { to: "/admin/dashboard", label: "Dashboard", icon: "⊞" },
  { to: "/admin/syllabus", label: "Syllabus", icon: "📋" },
  { to: "/admin/knowledge", label: "Knowledge", icon: "📚" },
  { to: "/admin/mcq", label: "MCQ System", icon: "❓" },
  { to: "/admin/mcq-tests", label: "MCQ Tests", icon: "📝" },
  { to: "/admin/video-tutor", label: "Video Tutor", icon: "🎥" },
  { to: "/admin/subjective", label: "Subjective Tests", icon: "📄" },
  { to: "/admin/skill-layer", label: "Skill Layer", icon: "⚙️" },
  { to: "/admin/students", label: "Students", icon: "👥" },
  { to: "/admin/analytics", label: "Analytics", icon: "📊" },
];

export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      {/* Sidebar */}
      <aside className="flex w-60 flex-shrink-0 flex-col border-r border-gray-200 bg-white">
        {/* Logo */}
        <div className="flex h-16 items-center gap-3 border-b border-gray-100 px-5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-500">
            <span className="text-sm font-bold text-white">N</span>
          </div>
          <span className="font-bold text-gray-900">NeuraFix AI</span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-4">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 px-5 py-2.5 text-sm transition-colors ${
                  isActive
                    ? "bg-brand-50 font-medium text-brand-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`
              }
            >
              <span className="w-5 text-center text-base leading-none">{item.icon}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* User footer */}
        <div className="border-t border-gray-100 p-4">
          <div className="mb-2 truncate text-xs text-gray-500">{user?.email}</div>
          <button
            onClick={handleLogout}
            className="w-full rounded-lg px-3 py-1.5 text-left text-sm text-gray-600 hover:bg-gray-100"
          >
            Sign out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-16 items-center justify-between border-b border-gray-200 bg-white px-6">
          <div className="text-sm font-medium text-gray-700">
            Welcome, {user?.full_name}
          </div>
          <span className="rounded-full bg-brand-50 px-2.5 py-0.5 text-xs font-medium text-brand-700">
            Admin
          </span>
        </header>

        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
