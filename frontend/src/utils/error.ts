import { AxiosError } from "axios";

/**
 * Safely extract a human-readable message from any thrown value.
 *
 * The backend returns errors in a couple of shapes:
 *   { error: { code, message } }           (AppException handler)
 *   { detail: "..." } | { detail: { message } }   (FastAPI / validation)
 * Network failures have no response at all. This collapses all of that into a
 * single string so callers never have to write deep optional chains.
 */
export function getErrorMessage(err: unknown, fallback = "Something went wrong."): string {
  if (err && typeof err === "object" && "isAxiosError" in err) {
    const ax = err as AxiosError<any>;
    if (ax.code === "ECONNABORTED") return "The request timed out. Please try again.";
    if (!ax.response) return "Cannot reach the server. Check your connection and try again.";

    const data = ax.response.data;
    if (typeof data === "string" && data.trim()) return data;
    if (data?.error?.message) return String(data.error.message);
    if (typeof data?.detail === "string") return data.detail;
    if (data?.detail?.message) return String(data.detail.message);
    if (Array.isArray(data?.detail) && data.detail[0]?.msg) return String(data.detail[0].msg);
    return ax.message || fallback;
  }
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err.trim()) return err;
  return fallback;
}
