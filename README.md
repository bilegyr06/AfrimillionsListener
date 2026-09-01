# Afrimillions Inactivity Listener

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
DATA_FOLDER=data
DB_PATH=app/notified_users.db
```

## Run

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload
```

The service watches `data/` and runs the notification cycle whenever a CSV file is created or modified.

### Manual trigger

```powershell
curl -X POST http://localhost:8000/trigger
```

## How it works

1. `watcher.py` detects CSV changes in `data/`
2. `processor.py` loads `Logins_*.csv` and `Registrations_*.csv`, finds the last login per user, and flags users inactive for >48h
3. `database.py` tracks notified users in SQLite (dedupe + cooldown via `next_available_at`)
4. `messager.py` sends SMS via Termii and records the result