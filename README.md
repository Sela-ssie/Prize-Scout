# Prize Scout

Project that searches YouTube videos and descriptions for giveaways and extracts giveaway details (deadline, prizes, how to enter) and returns structured results.

This repo contains a minimal FastAPI backend and a static frontend to query the API.

Quick start

1. Copy `.env.example` to `.env` and set `YOUTUBE_API_KEY` (and optionally `DATABASE_URL`).
2. Create a virtual environment and install deps:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

3. Run the API:

```powershell
uvicorn src.main:app --reload
```

4. Run the frontend (below) and try a search.

Frontend (development with Vite)

1. Install node dependencies inside `frontend/`:

```powershell
cd frontend
npm install
```

2. Start the dev server (it proxies `/api` to `http://localhost:8000`):

```powershell
npm run dev
```

3. Open the Vite dev URL (usually http://localhost:5173) and try searches.

To create a production build:

```powershell
cd frontend
npm run build
```

Persistence (SQLite)

This project defaults to using SQLite for persistence. To enable persistence, set `DATABASE_URL` or let the app use the default `prizes.db`:

```powershell
# in your shell (optional; default used automatically if not set)
$env:DATABASE_URL = 'sqlite:///./prizes.db'

# start backend
uvicorn src.main:app --reload
```

Saved giveaways endpoint

- The API exposes `GET /api/giveaways` which returns saved giveaways persisted in the DB.
- The React frontend includes a "Load saved giveaways" button to fetch and display these.



Notes
- The YouTube Data API requires an API key set in `YOUTUBE_API_KEY`.
- This is an initial scaffold; extractor heuristics are intentionally conservative.
- See `CLAUDE.md` for the quota constraint, architecture direction, and phase plan.
- `prizes.db` is not tracked in git. A database written before Phase 0 uses an
  outdated schema and will be rejected at startup with instructions.
