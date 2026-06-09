import type { ReactNode } from "react";
import { cn } from "./cn";

type Tone = "brand" | "accent" | "success" | "warning" | "danger" | "info" | "neutral";

const TONES: Record<Tone, string> = {
  brand: "bg-brand-50 text-brand-700 ring-brand-100",
  accent: "bg-accent-50 text-accent-700 ring-accent-100",
  success: "bg-success-50 text-success-700 ring-success-100",
  warning: "bg-warning-50 text-warning-700 ring-warning-100",
  danger: "bg-danger-50 text-danger-700 ring-danger-100",
  info: "bg-info-50 text-info-700 ring-info-100",
  neutral: "bg-gray-100 text-gray-600 ring-gray-200",
};

export function Badge({
  tone = "neutral",
  icon,
  children,
  className,
}: {
  tone?: Tone;
  icon?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/** Maps the platform's status strings to a Badge tone + label. */
const STATUS_MAP: Record<string, { tone: Tone; label?: string }> = {
  active: { tone: "success" },
  completed: { tone: "success" },
  generated: { tone: "success" },
  approved: { tone: "success" },
  checked: { tone: "success" },
  ready: { tone: "success" },
  passed: { tone: "success" },
  draft: { tone: "neutral" },
  archived: { tone: "neutral" },
  inactive: { tone: "neutral" },
  none: { tone: "neutral" },
  queued: { tone: "info" },
  processing: { tone: "info" },
  generating: { tone: "info" },
  in_progress: { tone: "info", label: "in progress" },
  uploaded: { tone: "info" },
  retrying: { tone: "warning" },
  shortage: { tone: "warning" },
  needs_reupload: { tone: "warning", label: "needs reupload" },
  pending: { tone: "warning" },
  passed_with_warning: { tone: "warning", label: "warning" },
  submitted: { tone: "brand" },
  failed: { tone: "danger" },
  rejected: { tone: "danger" },
  cancelled: { tone: "danger" },
  poor: { tone: "danger" },
};

export function StatusBadge({ status, className }: { status: string; className?: string }) {
  const key = (status || "").toLowerCase();
  const entry = STATUS_MAP[key] ?? { tone: "neutral" as Tone };
  const label = entry.label ?? key.replace(/_/g, " ");
  return (
    <Badge tone={entry.tone} className={cn("capitalize", className)}>
      {label}
    </Badge>
  );
}
