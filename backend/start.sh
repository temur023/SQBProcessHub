#!/usr/bin/env bash
# SQB Process Hub Backend — Dev start script
set -e

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
  echo "▶ Creating Python virtual environment..."
  python3 -m venv venv
fi

echo "▶ Installing dependencies..."
venv/bin/pip install -r requirements.txt --quiet

echo "▶ Starting SQB Process Hub API on http://localhost:8000"
echo "   Swagger docs: http://localhost:8000/api/docs"
echo "   ReDoc:        http://localhost:8000/api/redoc"
echo ""

PYTHONPATH=. venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
