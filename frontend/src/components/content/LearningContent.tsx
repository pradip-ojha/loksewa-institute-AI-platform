import type { ReactNode } from "react";
import { CheckCircle2, Lightbulb, Target, BookMarked, Sparkles } from "lucide-react";
import { cn } from "../ui/cn";
import { RichText } from "./RichText";

/** Bulleted key-points list with brand check markers. Each item may contain markdown. */
export function KeyPointsList({ items }: { items?: string[] | null }) {
  if (!items?.length) return null;
  return (
    <ul className="space-y-2">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2.5">
          <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-brand-500" />
          <span className="font-deva text-sm leading-relaxed text-gray-700">
            <RichText size="sm" className="prose-p:my-0 inline">
              {item}
            </RichText>
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Highlighted callout box for exam-focused points. */
export function ExamPointCallout({ items }: { items?: string[] | null }) {
  if (!items?.length) return null;
  return (
    <div className="rounded-lg border border-warning-100 bg-warning-50 p-4">
      <div className="mb-2 flex items-center gap-2 text-warning-700">
        <Target className="h-4 w-4" />
        <h4 className="text-sm font-semibold">परीक्षा केन्द्रित बुँदाहरू · Exam Focus</h4>
      </div>
      <ul className="space-y-1.5">
        {items.map((item, i) => (
          <li key={i} className="flex gap-2 font-deva text-sm leading-relaxed text-gray-700">
            <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning-500" />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Glossary of "term — meaning" strings. Splits on the first em/en dash or hyphen. */
export function TermsGlossary({ items }: { items?: string[] | null }) {
  if (!items?.length) return null;
  const parsed = items.map((raw) => {
    const m = raw.match(/^(.*?)\s*[—–-]\s*(.*)$/);
    return m ? { term: m[1].trim(), meaning: m[2].trim() } : { term: raw.trim(), meaning: "" };
  });
  return (
    <dl className="grid gap-2.5 sm:grid-cols-2">
      {parsed.map((t, i) => (
        <div key={i} className="rounded-xl border border-gray-100 bg-gray-50/60 p-3">
          <dt className="font-deva text-sm font-semibold text-brand-700">{t.term}</dt>
          {t.meaning && <dd className="mt-0.5 font-deva text-sm text-gray-600">{t.meaning}</dd>}
        </div>
      ))}
    </dl>
  );
}

interface PossibleQuestions {
  mcqs?: Array<{ question: string; options?: string[]; answer?: string }>;
  short?: string[];
  long?: string[];
}

/** Practice-question preview built from the structured `possible_questions` object. */
export function PossibleQuestionsCard({ data }: { data?: PossibleQuestions | null }) {
  if (!data) return null;
  const { mcqs, short, long } = data;
  if (!mcqs?.length && !short?.length && !long?.length) return null;
  return (
    <div className="space-y-4">
      {!!mcqs?.length && (
        <div className="space-y-3">
          {mcqs.map((q, i) => (
            <div key={i} className="rounded-xl border border-gray-100 bg-white p-3.5 shadow-card">
              <p className="font-deva text-sm font-medium text-gray-800">
                {i + 1}. {q.question}
              </p>
              {!!q.options?.length && (
                <ul className="mt-2 space-y-1">
                  {q.options.map((opt, oi) => {
                    const letter = opt.trim().charAt(0).toUpperCase();
                    const correct = q.answer && letter === q.answer.trim().charAt(0).toUpperCase();
                    return (
                      <li
                        key={oi}
                        className={cn(
                          "rounded-lg px-2.5 py-1.5 font-deva text-sm",
                          correct
                            ? "bg-success-50 font-medium text-success-700 ring-1 ring-success-100"
                            : "text-gray-600",
                        )}
                      >
                        {opt}
                        {correct && <CheckCircle2 className="ml-1.5 inline h-3.5 w-3.5" />}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          ))}
        </div>
      )}
      {!!short?.length && <QuestionGroup title="छोटो उत्तर · Short answer" items={short} />}
      {!!long?.length && <QuestionGroup title="लामो उत्तर · Long answer" items={long} />}
    </div>
  );
}

function QuestionGroup({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-xl border border-gray-100 bg-gray-50/60 p-3.5">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{title}</h4>
      <ol className="list-decimal space-y-1 pl-4">
        {items.map((q, i) => (
          <li key={i} className="font-deva text-sm text-gray-700">
            {q}
          </li>
        ))}
      </ol>
    </div>
  );
}

interface Section {
  section: string;
  awarded: number;
  max: number;
  status: string;
  note?: string;
}

const SECTION_STATUS: Record<string, { bar: string; chip: string }> = {
  correct: { bar: "bg-success-500", chip: "bg-success-50 text-success-700" },
  partial: { bar: "bg-warning-500", chip: "bg-warning-50 text-warning-700" },
  wrong: { bar: "bg-danger-500", chip: "bg-danger-50 text-danger-700" },
};

/** Per-criterion breakdown for subjective answers: awarded/max bars + status. */
export function SectionBreakdown({ sections }: { sections?: Section[] | null }) {
  if (!sections?.length) return null;
  return (
    <div className="space-y-2.5">
      {sections.map((s, i) => {
        const style = SECTION_STATUS[s.status?.toLowerCase()] ?? SECTION_STATUS.partial;
        const pct = s.max > 0 ? Math.max(0, Math.min(100, (s.awarded / s.max) * 100)) : 0;
        return (
          <div key={i}>
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="font-deva text-sm font-medium text-gray-700">{s.section}</span>
              <div className="flex items-center gap-2">
                <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium capitalize", style.chip)}>
                  {s.status}
                </span>
                <span className="text-xs font-semibold tabular-nums text-gray-500">
                  {s.awarded}/{s.max}
                </span>
              </div>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-gray-100">
              <div className={cn("h-full rounded-full transition-all", style.bar)} style={{ width: `${pct}%` }} />
            </div>
            {s.note && (
              <p className="mt-1 font-deva text-xs leading-relaxed text-gray-600">{s.note}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Small section header with an icon, used inside content tabs/cards. */
export function ContentSectionTitle({ icon, children }: { icon?: ReactNode; children: ReactNode }) {
  return (
    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-gray-900">
      <span className="text-brand-500">{icon ?? <Sparkles className="h-4 w-4" />}</span>
      {children}
    </h3>
  );
}

export const ContentIcons = { Lightbulb, Target, BookMarked, Sparkles, CheckCircle2 };
