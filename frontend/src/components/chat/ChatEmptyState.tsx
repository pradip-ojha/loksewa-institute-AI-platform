import type { ReactNode } from "react";
import { SuggestionChips } from "./SuggestionChips";

interface ChatEmptyStateProps {
  icon: ReactNode;
  title: string;
  description: string;
  starters: string[];
  onAsk: (question: string) => void;
}

/** Welcome block for an empty chat, with tappable starter questions. */
export function ChatEmptyState({ icon, title, description, starters, onAsk }: ChatEmptyStateProps) {
  return (
    <div className="rounded-xl border border-brand-100 bg-gradient-to-b from-brand-50/70 to-white p-6 text-center">
      <div className="mx-auto mb-2 flex h-11 w-11 items-center justify-center rounded-full bg-brand-100 text-brand-600">
        {icon}
      </div>
      <p className="text-sm font-semibold text-gray-800">{title}</p>
      <p className="mt-1 text-xs text-gray-500 font-deva">{description}</p>
      <SuggestionChips items={starters} onSelect={onAsk} tone="brand" className="mt-3 justify-center" />
    </div>
  );
}
