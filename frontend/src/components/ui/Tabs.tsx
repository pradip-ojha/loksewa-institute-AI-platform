import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { cn } from "./cn";

export interface TabItem {
  id: string;
  label: ReactNode;
  icon?: ReactNode;
  count?: number;
}

export function Tabs({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex gap-1 overflow-x-auto border-b border-gray-200 scrollbar-thin", className)}>
      {items.map((item) => {
        const active = item.id === value;
        return (
          <button
            key={item.id}
            onClick={() => onChange(item.id)}
            className={cn(
              "relative flex items-center gap-1.5 whitespace-nowrap px-3.5 py-2.5 text-sm font-medium transition-colors",
              active ? "text-brand-700" : "text-gray-500 hover:text-gray-800",
            )}
          >
            {item.icon}
            {item.label}
            {item.count !== undefined && (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-xs font-semibold",
                  active ? "bg-brand-100 text-brand-700" : "bg-gray-100 text-gray-500",
                )}
              >
                {item.count}
              </span>
            )}
            {active && (
              <motion.div
                layoutId="tab-underline"
                className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-brand-500"
              />
            )}
          </button>
        );
      })}
    </div>
  );
}
