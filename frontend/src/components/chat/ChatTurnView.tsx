import type { ReactNode } from "react";
import { GraduationCap } from "lucide-react";
import { RichText } from "../content/RichText";
import { TypingDots } from "./TypingDots";
import { SuggestionChips } from "./SuggestionChips";

export interface ChatTurnViewProps {
  /** Omit/empty for answer-only turns (e.g. a seeded assistant greeting). */
  question?: string;
  answer?: string;
  loading?: boolean;
  error?: string;
  /** Slot rendered above the answer text (e.g. a detected-topic pill). */
  beforeAnswer?: ReactNode;
  /** Slot rendered below the answer text (e.g. seekable video segment chips). */
  afterAnswer?: ReactNode;
  followUps?: string[];
  onFollowUp?: (question: string) => void;
}

/** One chat turn: right-aligned student bubble + left-aligned assistant bubble. */
export function ChatTurnView({
  question,
  answer,
  loading,
  error,
  beforeAnswer,
  afterAnswer,
  followUps,
  onFollowUp,
}: ChatTurnViewProps) {
  return (
    <div className="space-y-2">
      {question && (
        <div className="flex justify-end">
          <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand-600 px-4 py-2 text-sm text-white shadow-sm font-deva">
            {question}
          </div>
        </div>
      )}

      <div className="flex items-start gap-2">
        <div className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-brand-700">
          <GraduationCap className="h-4 w-4" />
        </div>
        <div className="max-w-[88%] rounded-2xl rounded-tl-sm bg-white p-4 shadow-sm ring-1 ring-gray-100">
          {loading && <TypingDots />}
          {error && <p className="text-sm text-danger-600">{error}</p>}
          {answer !== undefined && (
            <>
              {beforeAnswer}
              <RichText size="sm">{answer}</RichText>
              {afterAnswer}
              {followUps && followUps.length > 0 && onFollowUp && (
                <div className="mt-3 border-t border-gray-100 pt-3">
                  <p className="mb-1.5 text-xs font-medium text-gray-400">सम्भावित प्रश्न</p>
                  <SuggestionChips items={followUps} onSelect={onFollowUp} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
