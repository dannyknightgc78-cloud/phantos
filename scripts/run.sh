#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d backend/.venv ]; then
  python3 -m venv backend/.venv
fi
source backend/.venv/bin/activate
pip install -U pip
pip install -r backend/requirements.txt

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install)
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — edit Phantom paths before generating."
fi

echo "Starting Phantos backend on :8787 and frontend on :5173"
(
  cd backend
  source .venv/bin/activate
  uvicorn main:app --host 0.0.0.0 --port 8787 --reload
) &
BACKEND_PID=$!

(
  cd frontend
  npm run dev -- --host 0.0.0.0 --port 5173
) &
FRONTEND_PID=$!

trap 'kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' EXIT
wait
