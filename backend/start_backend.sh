#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -d "../venv" ]; then
  python3 -m venv ../venv
fi

../venv/bin/python -m pip install --upgrade pip >/dev/null
../venv/bin/python -m pip install -r requirements.txt

echo "Backend listo en http://0.0.0.0:5000"
echo "Si OpenOCD/GPIO pide permisos, ejecuta este archivo con sudo."
../venv/bin/python app.py
