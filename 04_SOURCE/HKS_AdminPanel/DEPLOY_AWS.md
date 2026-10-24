# Deploying HKS Admin Panel on AWS (or any VPS)

## 1. EC2 instance
* Ubuntu 22.04/24.04 `t3.micro` (or any VM with Python 3.10+).
* Security group inbound: **TCP 8000** (panel) from *your IP only* if possible,
  plus SSH 22 from your IP.

## 2. Run the panel
```bash
scp HKS_AdminPanel.zip ubuntu@SERVER:~/
ssh ubuntu@SERVER
sudo apt update && sudo apt install -y python3-venv
unzip HKS_AdminPanel.zip && cd HKS_AdminPanel
chmod +x run_server.sh
./run_server.sh --port 8000 --ssl-cert certs/server.pem --ssl-key certs/key.pem
```

### TLS
```bash
python3 generate_ssl.py        # self-signed (fine — agents pin it)
# or use a real cert (Let's Encrypt): point --ssl-cert/--ssl-key at fullchain/privkey
```

### Keep it running (systemd)
```ini
# /etc/systemd/system/hks-panel.service
[Unit]
Description=HKS Admin Panel
After=network.target

[Service]
WorkingDirectory=/home/ubuntu/HKS_AdminPanel
ExecStart=/home/ubuntu/HKS_AdminPanel/.venv/bin/python -m server.main --host 0.0.0.0 --port 8000 --ssl-cert certs/server.pem --ssl-key certs/key.pem
Restart=always
RestartSec=5
User=ubuntu

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now hks-panel
```

## 3. Point the laptop at it
In `config.ini` next to the client `setup.bat`:
```ini
server_url=wss://SERVER-IP:8000/ws/agent
token=<HKS_AGENT_TOKEN from the panel .env / Settings page>
```
Run `setup.bat` on the laptop.

## 4. Optional: AWS RDS MySQL instead of the local SQLite
The panel keeps users/logs in SQLite by default (`data/hks.db`). To move the
*agent-facing* SQL feature to RDS, just use the Database section in the panel:
engine = MySQL, host = your RDS endpoint, credentials — queries run **from the
laptop** over its own connection. Ensure the RDS security group allows the
laptop's outbound IP.

## 5. Hardening checklist
- [ ] Security group: port 8000 restricted to your IP where possible
- [ ] TLS enabled (`https://` in browser, `wss://` for agents)
- [ ] Strong admin password (the panel auto-bans brute force)
- [ ] Rotate `HKS_AGENT_TOKEN` if a config file ever leaks (edit `.env`, restart, re-run client setup)
- [ ] Keep OS patched; consider fail2ban on SSH
