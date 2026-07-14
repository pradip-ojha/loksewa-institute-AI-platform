import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import { cn } from "./cn";
import { EmptyState, Skeleton } from "./Feedback";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Custom cell renderer. Falls back to accessor()/String(row[key]). */
  render?: (row: T) => ReactNode;
  /** Sort/search value for the cell. Enables client sort when sortable. */
  accessor?: (row: T) => string | number;
  sortable?: boolean;
  width?: string;
  align?: "left" | "right" | "center";
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  emptyState?: ReactNode;
  /** Enable client-side filtering across searchKeys. */
  searchable?: boolean;
  searchQuery?: string;
  searchKeys?: (row: T) => Array<string | number | null | undefined>;
  onRowClick?: (row: T) => void;
  toolbar?: ReactNode;
  footer?: ReactNode;
  className?: string;
}

const ALIGN = { left: "text-left", right: "text-right", center: "text-center" } as const;

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading,
  emptyState,
  searchable,
  searchQuery,
  searchKeys,
  onRowClick,
  toolbar,
  footer,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(null);

  const filtered = useMemo(() => {
    if (!searchable || !searchQuery?.trim()) return rows;
    const q = searchQuery.trim().toLowerCase();
    const keys = searchKeys ?? ((row: T) => columns.map((c) => c.accessor?.(row) ?? ""));
    return rows.filter((row) =>
      keys(row).some((v) => String(v ?? "").toLowerCase().includes(q)),
    );
  }, [rows, searchable, searchQuery, searchKeys, columns]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.accessor) return filtered;
    const acc = col.accessor;
    return [...filtered].sort((a, b) => {
      const av = acc(a);
      const bv = acc(b);
      if (av < bv) return sort.dir === "asc" ? -1 : 1;
      if (av > bv) return sort.dir === "asc" ? 1 : -1;
      return 0;
    });
  }, [filtered, sort, columns]);

  const toggleSort = (key: string) =>
    setSort((s) =>
      s?.key === key
        ? { key, dir: s.dir === "asc" ? "desc" : "asc" }
        : { key, dir: "asc" },
    );

  return (
    <div className={cn("overflow-hidden rounded-lg border border-gray-200 bg-white", className)}>
      {toolbar && (
        <div className="flex flex-wrap items-center gap-3 border-b border-gray-200 px-4 py-3">
          {toolbar}
        </div>
      )}
      <div className="overflow-x-auto scrollbar-thin">
        <table className="w-full min-w-full border-collapse">
          <thead>
            <tr className="border-b border-gray-200 bg-gray-50">
              {columns.map((col) => {
                const active = sort?.key === col.key;
                return (
                  <th
                    key={col.key}
                    style={col.width ? { width: col.width } : undefined}
                    className={cn(
                      "px-4 py-2.5 text-xs font-medium text-gray-500",
                      ALIGN[col.align ?? "left"],
                    )}
                  >
                    {col.sortable && col.accessor ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col.key)}
                        className={cn(
                          "inline-flex items-center gap-1 transition-colors hover:text-gray-800",
                          col.align === "right" && "flex-row-reverse",
                          active && "text-gray-800",
                        )}
                      >
                        {col.header}
                        {active ? (
                          sort!.dir === "asc" ? (
                            <ArrowUp className="h-3 w-3" />
                          ) : (
                            <ArrowDown className="h-3 w-3" />
                          )
                        ) : (
                          <ArrowUpDown className="h-3 w-3 text-gray-400" />
                        )}
                      </button>
                    ) : (
                      col.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {loading ? (
              Array.from({ length: 5 }).map((_, i) => (
                <tr key={i} className="border-b border-gray-100 last:border-0">
                  {columns.map((col) => (
                    <td key={col.key} className="px-4 py-2.5">
                      <Skeleton className="h-4 w-24" />
                    </td>
                  ))}
                </tr>
              ))
            ) : sorted.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="p-0">
                  {emptyState ?? (
                    <EmptyState title="No records" className="border-0" />
                  )}
                </td>
              </tr>
            ) : (
              sorted.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(
                    "border-b border-gray-100 transition-colors last:border-0 hover:bg-gray-50",
                    onRowClick && "cursor-pointer",
                  )}
                >
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(
                        "px-4 py-2.5 text-sm text-gray-700",
                        ALIGN[col.align ?? "left"],
                      )}
                    >
                      {col.render
                        ? col.render(row)
                        : col.accessor
                          ? String(col.accessor(row))
                          : null}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {footer && <div className="border-t border-gray-200 px-4 py-3">{footer}</div>}
    </div>
  );
}
