// Shared NDJSON streaming client. axios can't expose a streaming response body in
// the browser, so streamed tutor endpoints use native fetch + ReadableStream and
// parse one JSON event per line ({type: "meta"|"delta"|"done"|"error", ...}).

const API_BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export interface StreamMeta {
  chat_session_id: string;
  detected_topic?: string | null;
  detected_subtopic_ids?: string[];
  selected_segments?: unknown[];
  [k: string]: unknown;
}

export interface StreamDone {
  language?: string;
  confidence?: number;
  follow_up_suggestions?: string[];
  [k: string]: unknown;
}

export interface StreamHandlers {
  onMeta?: (meta: StreamMeta) => void;
  onDelta?: (text: string) => void;
  onDone?: (done: StreamDone) => void;
  onError?: (message: string) => void;
}

const GENERIC_ERROR = "उत्तर ल्याउन सकिएन।";
// If the stream stalls (server accepted the connection but stops emitting before the
// `done`/`<<<META>>>` tail, or the network half-opens), don't hang the UI forever —
// abort and surface the generic error after this much silence between chunks.
const IDLE_TIMEOUT_MS = 60_000;

/** reader.read() racing an idle timer; returns "timeout" if no chunk arrives in time. */
async function readWithTimeout(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  ms: number,
): Promise<ReadableStreamReadResult<Uint8Array> | "timeout"> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<"timeout">((resolve) => {
    timer = setTimeout(() => resolve("timeout"), ms);
  });
  try {
    return await Promise.race([reader.read(), timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export async function streamNdjson(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem("access_token");
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    // An intentional abort (navigation / tab switch / new question) is not an error.
    if (signal?.aborted) return;
    handlers.onError?.(GENERIC_ERROR);
    return;
  }

  // Mirror the axios interceptor's 401 handling.
  if (res.status === 401) {
    localStorage.removeItem("access_token");
    localStorage.removeItem("user");
    window.location.href = "/login";
    return;
  }
  if (!res.ok || !res.body) {
    handlers.onError?.(GENERIC_ERROR);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const result = await readWithTimeout(reader, IDLE_TIMEOUT_MS);
      if (result === "timeout") {
        await reader.cancel().catch(() => {});
        handlers.onError?.(GENERIC_ERROR);
        return;
      }
      const { done, value } = result;
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf("\n")) !== -1) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) dispatch(line, handlers);
      }
    }
    const last = buf.trim();
    if (last) dispatch(last, handlers);
  } catch {
    // Aborted mid-stream (navigation / tab switch / new question) → not an error.
    if (signal?.aborted) return;
    handlers.onError?.(GENERIC_ERROR);
  }
}

function dispatch(line: string, handlers: StreamHandlers) {
  let evt: { type?: string; text?: string; message?: string };
  try {
    evt = JSON.parse(line);
  } catch {
    return;
  }
  switch (evt.type) {
    case "meta":
      handlers.onMeta?.(evt as StreamMeta);
      break;
    case "delta":
      handlers.onDelta?.(evt.text ?? "");
      break;
    case "done":
      handlers.onDone?.(evt as StreamDone);
      break;
    case "error":
      handlers.onError?.(evt.message ?? GENERIC_ERROR);
      break;
  }
}
