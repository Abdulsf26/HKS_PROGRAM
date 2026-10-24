#!/usr/bin/env bash
# HKS Admin Panel - run on Linux/macOS/AWS. First run auto-installs deps + TLS cert.
cd "$(dirname "$0")"
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -f certs/server.pem ]; then python generate_ssl.py; fi
ARGS=""
if [ -f certs/server.pem ]; then ARGS="--ssl-cert certs/server.pem --ssl-key certs/key.pem"; fi
exec python -m server.main --host 0.0.0.0 --port 8000 $ARGS "$@"
