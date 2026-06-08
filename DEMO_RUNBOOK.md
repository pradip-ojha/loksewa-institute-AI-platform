# NeuraFix AI — Demo Runbook (Local Windows)

One page to get the platform up and keep it up during the demo. Everything runs locally and talks to
managed cloud services (Neon, Upstash, Pinecone, R2, Azure OpenAI, Gemini), so a **stable internet
connection is required**.

---

## 1. Start (do this ~15 min before the demo)

From the project root in PowerShell:

```powershell
.\scripts\run-all.ps1
```

This runs the **preflight gate** first. If any dependency is unreachable it stops and prints exactly
what failed — fix that and re-run. If preflight passes, it opens three windows: **Backend**,
**Worker** (Celery + beat), **Frontend**.

To run the preflight on its own at any time (e.g. after a reboot or Wi-Fi change):

```powershell
python .\scripts\preflight.py
```

Manual start (if you prefer separate control):
```powershell
.\scripts\run-backend.ps1     # alembic upgrade head + uvicorn on :8000
.\scripts\run-worker.ps1      # celery worker + beat, --pool=solo
.\scripts\run-frontend.ps1    # vite dev server on :5173
```

**Order matters:** backend before worker/frontend (worker shares the DB; frontend proxies `/api` to
`:8000`).

### Verify before you present
- Browser: <http://localhost:8000/health/ready> → should return **`"status": "ready"`** with all
  dependencies `ok: true`. If it's `503 / degraded`, the named dependency is down — do not start.
- Browser: <http://localhost:5173> loads the login page.
- Log in as admin: **admin@neurafix.ai** (password = `DEFAULT_ADMIN_PASSWORD` in `backend/.env`).

---

## 2. 6-line smoke checklist (run once, quietly, before the client arrives)

1. **Login** as admin → create one test student → log in as that student in a second browser/incognito.
2. **MCQ**: upload a small MCQ PDF → extraction job completes → approve a few → make a 1-set blueprint
   → student attempts → immediate result with explanations.
3. **Subjective**: create a test (paper + per-question marks + model answer) → skill generation
   completes and the test **activates** → student uploads a short answer-sheet PDF → checking completes
   → checked PDF shows clean **Nepali** (no □ boxes) and section-wise marks.
4. **Video**: upload a **short (<5 min)** clip (+ optional slides PDF) → processing completes → student
   asks one Q&A question → answer returns in a few seconds.
5. **Skill Layer**: open Skill Builder for one agent → chat → draft → **approve** → version goes active.
6. **Reaper sanity**: confirm the Worker window is alive and beat is ticking (you'll see periodic
   keepalive / reaper log lines).

If all six pass, the demo path is green.

---

## 3. What to watch in the Worker window (`logs/worker.log`)

These are the early-warning strings. Seeing one occasionally that then recovers is fine; seeing it
repeatedly means trouble:

- `ExternalServiceError` — a cloud service (Pinecone / R2 / Azure / Gemini) call failed.
- `connection is closed` — a dropped DB connection (the retry/reaper should recover it).
- `Reap ... stale job` / `Startup recovery` — a job was force-failed; the UI will unstick.
- Gemini `rate-limited ... retrying in 60s` — a vision call is waiting out the per-minute limit
  (paid tier should rarely hit this).

Backend logs are in `logs/backend.log`.

---

## 4. Fast recovery (if something stalls mid-demo)

| Symptom | What's happening | Action |
|---|---|---|
| A job spinner won't finish (>2–3 min) | Worker may have died/wedged | Check the Worker window. The reaper fails stale jobs within ~2 min and the UI unsticks; if the window is dead, re-run `.\scripts\run-worker.ps1`. |
| Spinner forever, no progress at all | Worker not consuming / Redis blip | Look at `logs/worker.log`. Restart the worker window — on boot it auto-fails orphaned jobs and unsticks sheets. |
| Subjective checked PDF shows □□□ instead of Nepali | Bundled fonts missing | Run `python .\scripts\preflight.py` — the fonts check will flag it. Fonts live in `backend/app/processing/fonts/`. |
| Student gets "please re-upload a clearer scan" | Vision read no text (bad/blurry/upside-down scan) | Expected, safe behavior — upload a clearer sheet (up to 2 attempts). |
| Video upload fails immediately | FFmpeg not on PATH | `ffmpeg -version` in a terminal; install (`choco install ffmpeg`) and restart the worker. Preflight catches this. |
| Logged out unexpectedly | Token expired (24h) — unlikely in a demo | Log in again; nothing is lost. |
| `/health/ready` shows a dependency down | That cloud service / network is unreachable | Check internet; re-run preflight. Don't demo the affected feature until green. |

**Golden rule:** when in doubt, restart the **Worker** window first — it self-recovers orphaned jobs
on boot and is the most likely thing to wedge. The backend and frontend rarely need a restart.

---

## 5. Known hard dependencies (preflight checks all of these)
- **FFmpeg + ffprobe** on PATH — Video Tutor only.
- **Bundled Noto fonts** in `backend/app/processing/fonts/` — Subjective checked-PDF Nepali rendering.
- **Internet** to Neon / Upstash / Pinecone / R2 / Azure OpenAI / Gemini — everything.
