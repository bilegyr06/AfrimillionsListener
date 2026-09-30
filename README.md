# Afrimillions Listener

Afrimillions Listener is a customer-engagement service for AfriMillions. It monitors player activity data, identifies users who may need a reminder or welcome-back message, and sends timely SMS notifications through Termii.

The project was created to help the team reconnect with players before they become permanently inactive. Instead of relying on manual checks, it turns regularly exported activity data into a repeatable retention workflow.

## What it does

Afrimillions Listener can:

- identify players who have not logged in within a configured period
- send friendly inactivity reminders
- recognize returning users and send welcome-back messages
- avoid duplicate notifications and excessive message frequency
- process multiple SMS messages efficiently
- monitor CSV data updates and start notification cycles automatically
- provide operational controls for triggering, checking, and cancelling cycles

The system is designed to make outreach timely and consistent while reducing the risk of sending unnecessary or repetitive messages.

## How it works

1. Activity and customer data is exported as CSV files.
2. The service watches the configured data folder for new or updated files.
3. A notification cycle evaluates recent logins and other relevant activity.
4. Eligible users are selected according to the configured campaign rules.
5. Previously contacted users and cooldown periods are checked.
6. SMS messages are sent through Termii.
7. Notification history is saved locally so future cycles can avoid duplicates.

The service supports two main communication flows:

- **Inactive player reminders** — encourages players who have not logged in recently to return.
- **Welcome-back messages** — acknowledges players who have returned but may not yet have resumed activity.

## Setup

### Prerequisites

- Python 3.10 or newer
- pip
- A Termii account and API key
- CSV exports containing the activity data used by the service
- Docker Desktop, if you prefer to run the project with Docker

### 1. Clone the repository

```bash
git clone https://github.com/bilegyr06/AfrimillionsListener.git
cd AfrimillionsListener
```

### 2. Create a virtual environment

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On macOS or Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Configure the environment

Create a `.env` file in the project root. At minimum, configure the Termii credentials and the data paths:

```env
TERMII_API_KEY=your_termii_api_key
TERMII_SENDER_ID=Afrimillions

DATA_FOLDER=data
DB_PATH=database/notified_users.db

INACTIVITY_HOURS=48
COOLDOWN_HOURS=24
MAX_MESSAGES=0
MAX_CONCURRENCY=50

START_TIME=14:00
END_TIME=18:00

CSV_DOWNLOADER_ENABLED=false
```

`MAX_MESSAGES=0` means there is no campaign-level message limit. Adjust this value if you want to limit the number of messages sent in a cycle.

If the service should download data from ALOT BI automatically, configure the ALOT BI connection and enable the downloader:

```env
CSV_DOWNLOADER_ENABLED=true
ALOTBI_URL=https://your-alotbi-instance.example
ALOTBI_USERNAME=your_username
ALOTBI_PASSWORD=your_password
```

Keep `.env` out of version control. It contains credentials and other environment-specific configuration.

## Running locally

From the project root, start the FastAPI service with:

```bash
uvicorn app.main:app --app-dir backend --reload
```

The service will be available at:

- API: `http://localhost:8000`
- Interactive API documentation: `http://localhost:8000/docs`

Place the relevant CSV exports in the configured `data/` folder. The service will monitor that folder and react when supported files are created or updated.

## Running with Docker

The repository includes a Docker Compose configuration for running the backend and frontend together:

```bash
docker compose up --build
```

This starts:

- the backend API on port `8000`
- the frontend on port `3000`

The Docker setup mounts the `data/` and `database/` folders so CSV files and notification history remain available when containers are restarted.

For deployment using the prebuilt images, see the configuration and scripts in the `deploy/` directory.

## Operational endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Check whether the service is running |
| `/trigger` | POST | Start a notification cycle for enabled features |
| `/trigger/welcome` | POST | Start a welcome-back notification cycle |
| `/trigger/inactive` | POST | Start an inactivity reminder cycle |
| `/status` | GET | Check whether a cycle is running or being cancelled |
| `/cancel` | POST | Request cancellation of the current cycle |

Notification cycles can only begin during the configured sending window. If a cycle is already running, another cycle will not be started at the same time.

For the complete API, open `/docs` while the service is running.

## Project structure

```text
.
├── backend/           # FastAPI application and notification logic
├── frontend/          # Operator frontend
├── data/              # CSV data files watched by the service
├── database/          # SQLite notification and campaign state
├── deploy/            # Deployment configuration and scripts
├── docker-compose.yml  # Local Docker Compose configuration
└── README.md
```

## Why it was made

Player inactivity is easy to miss when it is reviewed manually. By automating the process, Afrimillions Listener helps the team:

- respond to inactivity sooner
- maintain a consistent customer-engagement process
- reduce manual monitoring of activity reports
- personalize outreach with the player’s name
- preserve a record of prior notifications
- scale SMS outreach without losing control over timing and volume

The intention is to make communication more helpful and more consistent—not to send messages indiscriminately. Cooldown rules, sending windows, message limits, and notification history help ensure that players receive relevant reminders at reasonable intervals.

## Security and operating notes

- Never commit Termii or ALOT BI credentials to the repository.
- Use environment variables for secrets and deployment-specific settings.
- Confirm that CSV exports contain the fields expected by the service before enabling automated sending.
- Test with a small message limit before running a large campaign.
- Review the configured sending window and message templates before production use.
