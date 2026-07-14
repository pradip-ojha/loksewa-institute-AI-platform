import type { ReactNode } from "react";
import { cn } from "./cn";

type Tone = "brand" | "accent" | "success" | "warning" | "danger" | "info" | "neutral";

// Tone tints the small icon only — surfaces stay white/bordered (enterprise look).
const ICON_TONES: Record<Tone, string> = {
  brand: "text-brand-600",
  accent: "text-brand-600",
  success: "text-success-600",
  warning: "text-warning-600",
  danger: "text-danger-600",
  info: "text-info-600",
  neutral: "text-gray-400",
};

export interface StatCardProps {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  tone?: Tone;
  hint?: ReactNode;
  /** Deprecated: entrance animation is no longer staggered. Kept for call-site compat. */
  index?: number;
}

export function StatCard({ label, value, icon, tone = "brand", hint }: StatCardProps) {
  return (
    <div className="animate-fade-in rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="text-xs font-medium text-gray-500">{label}</p>
        {icon && <span className={cn("flex-shrink-0", ICON_TONES[tone])}>{icon}</span>}
      </div>
      <p className="mt-2 text-2xl font-semibold tracking-tight tabular-nums text-gray-900">{value}</p>
      {hint && <p className="mt-1 text-xs text-gray-500">{hint}</p>}
    </div>
  );
}
