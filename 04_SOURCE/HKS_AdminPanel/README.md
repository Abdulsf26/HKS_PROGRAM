# HKS Admin Panel

Web dashboard + command hub for your HKS-managed laptop(s).

## Run it
```bash
python -m venv .venv            # or: setup_server.bat on Windows
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m server.main --host 0.0.0.0 --port 8000 \
    --ssl-cert certs/server.pem --ssl-key certs/key.pem
```
* First visit: `/setup` → create the admin account.
* Agent token: shown in **Settings** after login (stored in `.env`).
* `run_server.bat` (Windows) / `run_server.sh` (Linux) wrap all of this.

## Dashboard sections
Overview · Remote Screen · Files · Terminal · Apps · Media Finder · Database ·
Web Deploy · Camera & Mic · Media Player · Network · USB · Power · Scheduler ·
Logs & Logins · Errors · Users & Security · Settings — see FEATURES.md.

## Config
`.env` (auto-created on first run):
```
HKS_AGENT_TOKEN=<random>   # agents must present this token
```
CLI flags: `--host`, `--port`, `--ssl-cert`, `--ssl-key`.

## Data
Everything lives in `data/` — `hks.db` (SQLite) + `data/captures/*.jpg`.
Back up or delete as you like; tables auto-prune old telemetry.

## Testing
`tests/e2e_test.py` + `tests/mock_agent.py` in the repo root exercise the full
loop (login, rate limit, commands, downloads, WS frames) without Windows.
