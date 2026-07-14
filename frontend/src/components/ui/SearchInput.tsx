import { useEffect, useRef, useState } from "react";
import { Search, X } from "lucide-react";
import { cn } from "./cn";

export interface SearchInputProps {
  value?: string;
  onChange?: (v: string) => void;
  /** Called after a debounce (default 250ms). Use for expensive/server filtering. */
  onDebounced?: (v: string) => void;
  debounceMs?: number;
  placeholder?: string;
  className?: string;
}

/** Compact search box: leading icon + clear button + optional debounce. */
export function SearchInput({
  value,
  onChange,
  onDebounced,
  debounceMs = 250,
  placeholder = "Search…",
  className,
}: SearchInputProps) {
  const controlled = value !== undefined;
  const [internal, setInternal] = useState(value ?? "");
  const v = controlled ? value! : internal;
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!onDebounced) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => onDebounced(v), debounceMs);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [v]);

  const set = (next: string) => {
    if (!controlled) setInternal(next);
    onChange?.(next);
  };

  return (
    <div className={cn("relative h-9 w-64 max-w-full", className)}>
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
      <input
        value={v}
        onChange={(e) => set(e.target.value)}
        placeholder={placeholder}
        className="h-9 w-full rounded-md border border-gray-300 bg-white pl-9 pr-8 text-sm text-gray-900 placeholder:text-gray-400 transition-colors focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-600/20"
      />
      {v && (
        <button
          type="button"
          onClick={() => set("")}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 transition-colors hover:text-gray-700"
          aria-label="Clear search"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
