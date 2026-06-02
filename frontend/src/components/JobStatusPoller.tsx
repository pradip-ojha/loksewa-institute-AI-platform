import { useEffect, useRef, useState } from "react";
import api from "../services/api";

export interface JobState {
  id: string;
  job_type: string;
  status: "queued" | "processing" | "completed" | "failed" | "retrying" | "cancelled";
  progress_percent: number;
  current_step: string | null;
  error_message: string | null;
}

interface Props {
  jobId: string;
  onComplete?: (job: JobState) => void;
  onFail?: (job: JobState) => void;
  className?: string;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export function JobStatusPoller({ jobId, onComplete, onFail, className = "" }: Props) {
  const [job, setJob] = useState<JobState | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!jobId) return;

    const poll = async () => {
      try {
        const { data } = await api.get<JobState>(`/api/jobs/${jobId}`);
        setJob(data);
        if (TERMINAL.has(data.status)) {
          if (intervalRef.current) clearInterval(intervalRef.current);
          if (data.status === "completed") onComplete?.(data);
          else onFail?.(data);
        }
      } catch {
        // network error — keep polling
      }
    };

    poll();
    intervalRef.current = setInterval(poll, 2000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [jobId]);

  if (!job) return null;

  const statusColor: Record<string, string> = {
    queued: "bg-gray-400",
    processing: "bg-brand-500",
    completed: "bg-green-500",
    failed: "bg-red-500",
    retrying: "bg-yellow-400",
    cancelled: "bg-gray-400",
  };

  const labelColor: Record<string, string> = {
    queued: "text-gray-600",
    processing: "text-brand-700",
    completed: "text-green-700",
    failed: "text-red-700",
    retrying: "text-yellow-700",
    cancelled: "text-gray-600",
  };

  return (
    <div className={`rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 ${className}`}>
      <div className="mb-2 flex items-center justify-between">
        <span className={`text-sm font-medium capitalize ${labelColor[job.status]}`}>
          {job.status}
        </span>
        <span className="text-sm text-gray-500">{job.progress_percent}%</span>
      </div>

      {/* Progress bar */}
      <div className="h-2 overflow-hidden rounded-full bg-gray-100">
        <div
          className={`h-full rounded-full transition-all duration-500 ${statusColor[job.status]}`}
          style={{ width: `${job.progress_percent}%` }}
        />
      </div>

      {job.current_step && (
        <p className="mt-2 text-xs text-gray-500">{job.current_step}</p>
      )}
      {job.status === "failed" && job.error_message && (
        <p className="mt-2 text-xs text-red-600">{job.error_message}</p>
      )}
    </div>
  );
}
