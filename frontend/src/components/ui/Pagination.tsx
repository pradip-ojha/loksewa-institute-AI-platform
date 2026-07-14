import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "./cn";

export interface PaginationProps {
  page: number; // 1-based
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  className?: string;
}

export function Pagination({ page, pageSize, total, onPage, className }: PaginationProps) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const btn =
    "inline-flex h-8 items-center gap-1 rounded-md border border-gray-300 bg-white px-2.5 text-sm text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <div className={cn("flex items-center justify-between gap-3", className)}>
      <p className="text-xs tabular-nums text-gray-500">
        Showing {from}–{to} of {total}
      </p>
      <div className="flex items-center gap-1.5">
        <button className={btn} disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft className="h-4 w-4" />
          Prev
        </button>
        <button className={btn} disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
