import type { ReactNode } from "react";
import { cn } from "./cn";

export function PageHeader({
  title,
  description,
  icon,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-gray-200 pb-4",
        className,
      )}
    >
      <div className="flex items-start gap-2.5">
        {icon && <span className="mt-0.5 flex-shrink-0 text-gray-400">{icon}</span>}
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-gray-900">{title}</h1>
          {description && <p className="mt-0.5 text-sm text-gray-500">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}
