import { useRef, useState } from "react";
import api from "../../services/api";
import { JobStatusPoller, type JobState } from "../../components/JobStatusPoller";

const CONTEXTS = ["document", "answer_sheet", "video", "audio", "slides"] as const;

interface UploadResult {
  id: string;
  original_filename: string;
  display_name: string;
  mime_type: string;
  file_size: number;
}

export function AdminFileUploadTest() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [context, setContext] = useState<string>("document");
  const [displayName, setDisplayName] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [jobId, setJobId] = useState<string | null>(null);
  const [jobError, setJobError] = useState<string | null>(null);
  const [dispatching, setDispatching] = useState(false);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadResult(null);
    setSignedUrl(null);
    setUploadError(null);

    const form = new FormData();
    form.append("file", file);
    form.append("context", context);
    form.append("display_name", displayName || file.name);

    try {
      const { data } = await api.post<UploadResult>("/api/files/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setUploadResult(data);
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? "Upload failed";
      setUploadError(String(msg));
    } finally {
      setUploading(false);
    }
  };

  const handleGetUrl = async () => {
    if (!uploadResult) return;
    try {
      const { data } = await api.get<{ signed_url: string }>(`/api/files/${uploadResult.id}/url`);
      setSignedUrl(data.signed_url);
    } catch {
      setSignedUrl("Error fetching URL");
    }
  };

  const handleDispatchJob = async () => {
    setDispatching(true);
    setJobId(null);
    setJobError(null);
    try {
      const { data } = await api.post<{ id: string }>("/api/admin/jobs/test");
      setJobId(data.id);
    } catch {
      setJobError("Failed to dispatch test job.");
    } finally {
      setDispatching(false);
    }
  };

  const handleJobDone = (_job: JobState) => {};

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-semibold text-gray-900">Stage 3 — Infrastructure Test</h2>
        <p className="mt-1 text-sm text-gray-500">
          Temporary test page. Verifies R2 file upload and Celery job queue.
        </p>
      </div>

      {/* File Upload Section */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-100">
        <h3 className="mb-4 font-semibold text-gray-800">1. File Upload → R2</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Context</label>
            <select
              value={context}
              onChange={(e) => setContext(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            >
              {CONTEXTS.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Display Name</label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Optional — defaults to filename"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            />
          </div>
        </div>
        <div className="mt-4">
          <label className="mb-1 block text-sm font-medium text-gray-700">File</label>
          <input ref={fileRef} type="file" className="text-sm text-gray-600" />
        </div>
        <button
          onClick={handleUpload}
          disabled={uploading}
          className="mt-4 rounded-lg bg-brand-500 px-5 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {uploading ? "Uploading…" : "Upload to R2"}
        </button>

        {uploadError && (
          <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{uploadError}</div>
        )}

        {uploadResult && (
          <div className="mt-4 rounded-lg bg-green-50 p-4 text-sm">
            <p className="font-semibold text-green-800">Upload successful</p>
            <dl className="mt-2 space-y-1">
              <div className="flex gap-2"><dt className="text-gray-500 w-32">File ID:</dt><dd className="font-mono text-gray-800 break-all">{uploadResult.id}</dd></div>
              <div className="flex gap-2"><dt className="text-gray-500 w-32">Filename:</dt><dd className="text-gray-800">{uploadResult.original_filename}</dd></div>
              <div className="flex gap-2"><dt className="text-gray-500 w-32">MIME:</dt><dd className="text-gray-800">{uploadResult.mime_type}</dd></div>
              <div className="flex gap-2"><dt className="text-gray-500 w-32">Size:</dt><dd className="text-gray-800">{(uploadResult.file_size / 1024).toFixed(1)} KB</dd></div>
            </dl>
            <button
              onClick={handleGetUrl}
              className="mt-3 rounded-lg bg-white px-4 py-1.5 text-sm font-medium text-brand-600 ring-1 ring-brand-200 hover:bg-brand-50"
            >
              Get 60-min Signed URL
            </button>
            {signedUrl && (
              <div className="mt-2">
                <a
                  href={signedUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="break-all text-xs text-blue-600 underline"
                >
                  {signedUrl}
                </a>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Job Queue Section */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-gray-100">
        <h3 className="mb-4 font-semibold text-gray-800">2. Background Job Queue (Celery)</h3>
        <p className="mb-4 text-sm text-gray-500">
          Dispatches a simulate_job task. Watch it progress 0→100% via the poller below.
        </p>
        <button
          onClick={handleDispatchJob}
          disabled={dispatching}
          className="rounded-lg bg-brand-500 px-5 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
        >
          {dispatching ? "Dispatching…" : "Dispatch Test Job"}
        </button>

        {jobError && (
          <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{jobError}</div>
        )}

        {jobId && (
          <div className="mt-4">
            <p className="mb-2 text-xs text-gray-500">Job ID: <span className="font-mono">{jobId}</span></p>
            <JobStatusPoller jobId={jobId} onComplete={handleJobDone} onFail={handleJobDone} />
          </div>
        )}
      </div>
    </div>
  );
}
