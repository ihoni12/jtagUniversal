#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
