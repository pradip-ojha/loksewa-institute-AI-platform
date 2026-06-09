import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { cn } from "./cn";

type Tone = "brand" | "accent" | "success" | "warning" | "danger" | "info" | "neutral";

const GRADIENTS: Record<Tone, string> = {
  brand: "from-brand-500 to-brand-700",
  accent: "from-accent-500 to-accent-700",
  success: "from-success-500 to-success-700",
  warning: "from-warning-500 to-warning-700",
  danger: "from-danger-500 to-danger-700",
  info: "from-info-500 to-info-700",
  neutral: "from-slate-600 to-slate-800",
};

export interface StatCardProps {
  label: string;
  value: ReactNode;
  icon?: ReactNode;
  tone?: Tone;
  hint?: ReactNode;
  /** Index used to stagger the entrance animation. */
  index?: number;
}

export function StatCard({ label, value, icon, tone = "brand", hint, index = 0 }: StatCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.05 }}
      className={cn(
        "relative overflow-hidden rounded-2xl bg-gradient-to-br p-5 text-white shadow-card",
        GRADIENTS[tone],
      )}
    >
      <div className="absolute -right-4 -top-4 h-24 w-24 rounded-full bg-white/10" />
      <div className="absolute -bottom-8 -left-2 h-20 w-20 rounded-full bg-white/5" />
      <div className="relative flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-white/80">{label}</p>
          <p className="mt-1 text-3xl font-bold tracking-tight">{value}</p>
          {hint && <p className="mt-1 text-xs text-white/70">{hint}</p>}
        </div>
        {icon && (
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/20 backdrop-blur-sm">
            {icon}
          </div>
        )}
      </div>
    </motion.div>
  );
}
