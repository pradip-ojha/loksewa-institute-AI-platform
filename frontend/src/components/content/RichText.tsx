import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../ui/cn";

/**
 * Renders AI-generated learning content as formatted markdown (headings, bold key
 * terms, lists, tables) inside the tuned `prose-brand` typography theme. Plain text
 * without markdown still renders cleanly. Used everywhere AI prose appears: video
 * summaries, tutor answers, answer-sheet feedback, MCQ explanations.
 */
export const RichText = memo(function RichText({
  children,
  className,
  size = "base",
}: {
  children: string | null | undefined;
  className?: string;
  size?: "sm" | "base";
}) {
  const text = (children ?? "").trim();
  if (!text) return null;

  return (
    <div
      className={cn(
        "prose prose-brand max-w-none font-deva",
        size === "sm" ? "prose-sm" : "prose-sm sm:prose-base",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Open links safely in a new tab.
          a: ({ node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
          // Wrap tables so they scroll on mobile instead of overflowing.
          table: ({ node, ...props }) => (
            <div className="overflow-x-auto scrollbar-thin">
              <table {...props} />
            </div>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
