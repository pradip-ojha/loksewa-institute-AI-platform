/** Three bouncing dots shown while the assistant is thinking/streaming. */
export function TypingDots() {
  return (
    <div className="flex items-center gap-1 py-1" aria-label="सोच्दै">
      <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.3s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300 [animation-delay:-0.15s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-brand-300" />
    </div>
  );
}
