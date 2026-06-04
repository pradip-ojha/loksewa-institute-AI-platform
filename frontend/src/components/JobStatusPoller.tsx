import { useEffect, useRef, useState } from "react";
import api from "../services/api";

export interface JobState {
  id: string;
  job_type: string;
  status: "queued" | "processing" | "completed" | "failed" | "retrying" | "cancelled";
  progress_percent: number;
  current_step: string | null;
  error_message: string | null;
  output_reference?: { saved?: number; skipped?: number; replaced?: number } | null;
}

interface Props {
  jobId: string;
  onComplete?: (job: JobState) => void;
  onFail?: (job: JobState) => void;
  className?: string;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const BASE_INTERVAL = 2000;          // normal poll cadence
const MAX_POLLS = 600;               // ~20 min ceiling at base cadence
const MAX_CONSECUTIVE_ERRORS = 10;   // give up after this many failed polls in a row

type PollerError = "none" | "connection" | "timeout";

export function JobStatusPoller({ jobId, onComplete, onFail, className = "" }: Props) {
  const [job, setJob] = useState<JobState | null>(null);
  const [pollerError, setPollerError] = useState<PollerError>("none");
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!jobId) return;

    let cancelled = false;
    let polls = 0;
    let consecutiveErrors = 0;

    const schedule = (delay: number) => {
      timeoutRef.current = setTimeout(poll, delay);
    };

    const poll = async () => {
      if (cancelled) return;
      polls += 1;
      if (polls > MAX_POLLS) {
        setPollerError("timeout");
        return; // stop — job is taking unusually long; user can refresh
      }
      try {
        const { data } = await api.get<JobState>(`/api/jobs/${jobId}`);
        if (cancelled) return;
        consecutiveErrors = 0;
        setPollerError("none");
        setJob(data);
        if (TERMINAL.has(data.status)) {
          if (data.status === "completed") onComplete?.(data);
          else onFail?.(data);
          return; // terminal — stop polling
        }
        schedule(BASE_INTERVAL);
      } catch {
        if (cancelled) return;
        consecutiveErrors += 1;
        if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
          setPollerError("timeout");
          return; // give up after sustained failures
        }
        if (consecutiveErrors >= 3) setPollerError("connection");
        // Exponential backoff while the server/network is unreachable.
        const backoff = Math.min(BASE_INTERVAL * 2 ** (consecutiveErrors - 1), 30000);
        schedule(backoff);
      }
    };

    poll();
    return () => {
      cancelled = true;
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  if (pollerError === "timeout" && (!job || !TERMINAL.has(job.status))) {
    return (
      <div className={`rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 ${className}`}>
        <p className="text-sm font-medium text-yellow-700">Lost connection to the job</p>
        <p className="mt-1 text-xs text-gray-500">
          We stopped checking after repeated failures or a long wait. Refresh the page to resume tracking.
        </p>
      </div>
    );
  }

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
      {job.status === "completed" && job.output_reference?.skipped ? (
        <p className="mt-2 text-xs text-yellow-700">
          {(job.output_reference.saved ?? job.output_reference.replaced ?? 0)} saved,{" "}
          {job.output_reference.skipped} skipped (malformed and dropped).
        </p>
      ) : null}
      {pollerError === "connection" && !TERMINAL.has(job.status) && (
        <p className="mt-2 text-xs text-yellow-600">Reconnecting…</p>
      )}
      {job.status === "failed" && job.error_message && (
        <p className="mt-2 text-xs text-red-600">{job.error_message}</p>
      )}
    </div>
  );
}
