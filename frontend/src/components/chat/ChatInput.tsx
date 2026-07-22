import { Send } from "lucide-react";
import { cn } from "../ui/cn";

const VARIANTS = {
  // Floats above the mobile bottom nav; sits near the viewport bottom on desktop.
  floating: "sticky bottom-[calc(4.5rem+env(safe-area-inset-bottom))] lg:bottom-4 shadow-md",
  // Lives inside a card (subjective feedback panel) — no sticky positioning.
  embedded: "shadow-sm",
} as const;

interface ChatInputProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
  variant?: keyof typeof VARIANTS;
  className?: string;
}

/** The shared pill-shaped chat input with a round send button. */
export function ChatInput({
  value,
  onChange,
  onSubmit,
  disabled,
  placeholder = "प्रश्न सोध्नुहोस्…",
  variant = "floating",
  className,
}: ChatInputProps) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className={cn(
        "mt-4 flex items-center gap-2 rounded-full bg-white p-1.5 ring-1 ring-gray-200 transition-shadow focus-within:ring-brand-300",
        VARIANTS[variant],
        className,
      )}
    >
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="flex-1 rounded-full border-0 bg-transparent px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-0 font-deva"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-full bg-brand-600 text-white transition-colors hover:bg-brand-700 disabled:opacity-40"
        aria-label="Send"
      >
        <Send className="h-4 w-4" />
      </button>
    </form>
  );
}
