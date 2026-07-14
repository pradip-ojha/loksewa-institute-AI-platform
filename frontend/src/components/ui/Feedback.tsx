import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "./cn";

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-5 w-5 animate-spin text-brand-600", className)} />;
}

export function PageLoader({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-gray-400">
      <Spinner className="h-7 w-7" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed border-gray-300 bg-white px-6 py-12 text-center",
        className,
      )}
    >
      {icon && (
        <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-gray-100 text-gray-400">
          {icon}
        </div>
      )}
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-sm text-gray-500">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("shimmer rounded-lg bg-gray-200/70", className)} />;
}

export function Alert({
  tone = "danger",
  children,
  className,
}: {
  tone?: "danger" | "success" | "warning" | "info";
  children: ReactNode;
  className?: string;
}) {
  const tones = {
    danger: "bg-danger-50 text-danger-700 ring-danger-100",
    success: "bg-success-50 text-success-700 ring-success-100",
    warning: "bg-warning-50 text-warning-700 ring-warning-100",
    info: "bg-info-50 text-info-700 ring-info-100",
  };
  return (
    <div className={cn("rounded-lg px-3 py-2 text-sm ring-1 ring-inset", tones[tone], className)}>
      {children}
    </div>
  );
}
