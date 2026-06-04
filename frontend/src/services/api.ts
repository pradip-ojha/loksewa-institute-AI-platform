import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

// Default timeout for normal API calls so a stalled request can't hang the UI
// forever. File uploads pass a longer per-request timeout (see UPLOAD_TIMEOUT).
const api = axios.create({
  baseURL: API_BASE,
  headers: { "Content-Type": "application/json" },
  timeout: 60_000,
});

// Use as a per-request override on file uploads, which can legitimately take
// much longer than a normal API call: api.post(url, form, { timeout: UPLOAD_TIMEOUT })
export const UPLOAD_TIMEOUT = 600_000;

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes("/auth/login")) {
      localStorage.removeItem("access_token");
      localStorage.removeItem("user");
      window.location.href = "/login";
    }
    return Promise.reject(error);
  }
);

export default api;
