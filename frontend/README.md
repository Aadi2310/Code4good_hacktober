# VYOM+ frontend

The frontend uses the backend API at `/api/v1`. During local development, Vite
proxies those requests to `http://127.0.0.1:8000`.

Start the backend from `backend/`:

```powershell
python -m uvicorn vyom.api.app:app --reload --host 127.0.0.1 --port 8000
```

Then start the frontend from `frontend/`:

```powershell
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://127.0.0.1:5173/`. The upload, job status/result, review, field
edits, and JSON/CSV exports use the existing `/api/v1` endpoints. Exports use
the backend's `exportable` scope; records requiring review remain excluded.
