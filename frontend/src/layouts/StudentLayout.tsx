import { NavLink, Outlet } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/student/dashboard", label: "Home", icon: "⊞" },
  { to: "/student/mcq-tests", label: "MCQ Tests", icon: "❓" },
  { to: "/student/video-tutor", label: "Videos", icon: "🎥" },
  { to: "/student/subjective-tests", label: "Subjective", icon: "📄" },
  { to: "/student/results", label: "Results", icon: "📊" },
  { to: "/student/profile", label: "Profile", icon: "👤" },
];

export function StudentLayout() {
  return (
    <div className="flex min-h-screen flex-col bg-gray-50 pb-16">
      {/* Top header */}
      <header className="sticky top-0 z-10 flex h-14 items-center border-b border-gray-200 bg-white px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-500">
          <span className="text-sm font-bold text-white">N</span>
        </div>
        <span className="ml-2 font-semibold text-gray-900">NeuraFix AI</span>
      </header>

      <main className="flex-1 px-4 py-4">
        <Outlet />
      </main>

      {/* Bottom navigation bar */}
      <nav className="fixed bottom-0 left-0 right-0 z-10 flex h-16 border-t border-gray-200 bg-white">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center justify-center gap-0.5 text-xs transition-colors ${
                isActive ? "text-brand-600" : "text-gray-500 hover:text-gray-700"
              }`
            }
          >
            <span className="text-lg leading-none">{item.icon}</span>
            <span className="truncate">{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
