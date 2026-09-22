#!/usr/bin/env bash
set -e

cleanup() {
    echo "Shutting down..."
    kill "$UV_PID" 2>/dev/null
    wait "$UV_PID" 2>/dev/null
    exit 0
}
trap cleanup SIGTERM SIGINT

echo "Starting FastAPI (uvicorn)..."
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
UV_PID=$!

echo "All services running (uvicorn=$UV_PID)"
wait "$UV_PID"