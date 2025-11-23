#!/bin/bash

# Function to kill processes on exit
cleanup() {
    echo "Stopping services..."
    kill $(jobs -p)
    exit
}

# Trap Ctrl+C (SIGINT) and kill background jobs
trap cleanup SIGINT SIGTERM

echo "--- Audibound Studio Startup ---"

# Source .env to ensure variables are available to all child processes
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# 1. Start Redis (if not running)
if ! pgrep -x "redis-server" > /dev/null
then
    echo "[1/3] Starting Redis..."
    redis-server --stop-writes-on-bgsave-error no &
else
    echo "[1/3] Redis is already running."
fi

# Wait a moment for Redis to warm up
sleep 2

# 2. Start Celery Worker
echo "[2/3] Starting Celery Worker..."
celery -A src.worker.celery_app worker --loglevel=info &

# 3. Start FastAPI Server
echo "[3/3] Starting Web Server..."
uvicorn src.main:app --reload &

# Keep script running to maintain background jobs
wait
