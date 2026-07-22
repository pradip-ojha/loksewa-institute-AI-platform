const TONES = {
  // Follow-up questions under an answer.
  neutral: "rounded-full bg-gray-100 px-3 py-1 text-xs text-gray-700 transition-colors hover:bg-gray-200 font-deva",
  // Starter questions in an empty chat.
  brand:
    "rounded-full bg-white px-3 py-1.5 text-xs text-brand-700 shadow-sm ring-1 ring-brand-100 transition-colors hover:bg-brand-50 font-deva",
} as const;

interface SuggestionChipsProps {
  items: string[];
  onSelect: (text: string) => void;
  tone?: keyof typeof TONES;
  className?: string;
}

/** Tappable question chips (starter questions / follow-up suggestions). */
export function SuggestionChips({ items, onSelect, tone = "neutral", className = "" }: SuggestionChipsProps) {
  if (items.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-2 ${className}`}>
      {items.map((text, i) => (
        <button key={i} type="button" onClick={() => onSelect(text)} className={TONES[tone]}>
          {text}
        </button>
      ))}
    </div>
  );
}
