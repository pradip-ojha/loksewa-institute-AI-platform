import { NavLink } from "react-router-dom";
import type { LucideIcon } from "lucide-react";

export type SidebarNavItem = { to: string; label: string; icon: LucideIcon };

/** Sidebar nav link shared by the admin and student desktop sidebars. */
export function SidebarLink({ item, onClick }: { item: SidebarNavItem; onClick?: () => void }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      onClick={onClick}
      className={({ isActive }) =>
        `group flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors ${
          isActive
            ? "bg-brand-50 font-medium text-brand-700"
            : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
        }`
      }
    >
      {({ isActive }) => (
        <>
          <Icon
            className={`h-4 w-4 flex-shrink-0 ${
              isActive ? "text-brand-600" : "text-gray-400 group-hover:text-gray-600"
            }`}
          />
          {item.label}
        </>
      )}
    </NavLink>
  );
}
