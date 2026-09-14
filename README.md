# Afrimillions Listener

A FastAPI service that watches the `data/` folder for CSV updates, finds users who haven't logged in for the last 48 hours, and sends them an SMS via [Termii](https://termii.com).

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
CSV_DOWNLOADER_ENABLED=false   # set true to auto-download CSVs from ALOT BI
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

The service watches `data/` and starts the notification cycle whenever a CSV file is created or modified. If a cycle is already running when a new file change arrives, the change is skipped.

## How it works

1. `watcher.py` detects CSV changes in `data/` and schedules a cycle on the event loop
2. `processor.py` loads `Logins_*.csv` and `Registrations_*.csv`, finds the last login per user, and flags users inactive for >48h
3. `database.py` tracks notified users in SQLite (dedupe + cooldown via `next_available_at`)
4. `messager.py` sends SMS via Termii asynchronously, up to `MAX_CONCURRENCY` at a time
5. Cancelling (`/cancel` or Ctrl+C) stops pending sends; already-sent notifications are still saved
