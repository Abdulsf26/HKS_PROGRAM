# HKS Security Notes

## Security controls

| Area | Control |
|---|---|
| Panel login | PBKDF2-SHA256 password hashing, login rate limiting, session revocation |
| API | Rate limits, CSRF protection on mutations, HttpOnly session cookies |
| Roles | Administrator and read-only observer authorization |
| Agent connection | Random agent token, TLS, and certificate pinning |
| Remote input | Failsafe behavior on errors and disconnects |
| USB | Keyboard and pointer device classes are protected from disable actions |
| Audit | Administrative actions and security events are recorded locally |
| Captures | Captures are stored on the configured server; set retention and access controls accordingly |

## Deployment guidance

- Install only on computers you own or are explicitly authorized to administer.
- Use a unique administrator password and protect the agent token and private TLS keys.
- Prefer HTTPS. Do not expose control ports directly to the public internet; use a properly secured VPN or access gateway.
- Treat the dashboard as a high-privilege administration surface. Restrict network reachability and administrator accounts.
- Back up databases securely. Do not commit `.env`, tokens, passwords, TLS keys, captures, databases, or logs.
- If compromise is suspected, revoke sessions, rotate the admin password and agent token, and inspect the host before reconnecting agents.

## Platform limits

Camera access depends on Windows privacy settings and available hardware. Windows secure desktop and Ctrl+Alt+Del restrictions cannot be bypassed by ordinary user input. Some controls require elevation and may be blocked by Windows policy.
