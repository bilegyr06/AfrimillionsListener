# Afrimillions Listener

A FastAPI service that ingests CSV data into the `data/` folder, finds users who haven't logged in for the last 48 hours, and sends them an SMS via [Termii](https://termii.com). Notification cycles are always operator-initiated (via `/trigger*` or the v2 Campaign Run dispatch flow) — file changes alone never trigger sends.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

Create a `.env` file (see `.env` for defaults):

```
TERMII_API_KEY=your_key_here
TERMII_SENDER_ID=Afrimillions
INACTIVITY_HOURS=48      # inactivity threshold
COOLDOWN_HOURS=24        # min hours between messages
MAX_MESSAGES=0           # 0 = unlimited
MAX_CONCURRENCY=50       # parallel SMS sends per cycle
DATA_FOLDER=data
DB_PATH=app/notified_users.db
```

## Run

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload
```

### Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/trigger` | POST | Start a notification cycle (returns immediately, runs in background) |
| `/status` | GET | Check if a cycle is running / cancellation requested |
| `/cancel` | POST | Cancel the running cycle |
| `/health` | GET | Liveness check |

CSVs are ingested through the manual upload endpoint or by placing files in `data/`. Cycles run only when an operator starts them; changing a file in `data/` never starts a cycle on its own.

## How it works

1. CSVs land in `data/` via manual upload; `ingestion.py` validates and stores them under a recognized dataset prefix
2. `processor.py` loads `Login_*.csv` and `Registrations_*.csv`, finds the last login per user, and flags users inactive for >48h
3. `database.py` tracks notified users in SQLite (dedupe + cooldown via `next_available_at`)
4. `sms.py` sends SMS via Termii asynchronously, up to `MAX_CONCURRENCY` at a time
5. Cancelling (`/cancel` or Ctrl+C) stops pending sends; already-sent notifications are still saved
