# HKS Security Notes

## What's implemented
| Layer | Control |
|---|---|
| Panel login | PBKDF2-SHA256 (200k iters), per-IP limiter (6/5min) → 15-min ban |
| API | 300 req/min per IP, CSRF token on all mutations, HttpOnly cookie |
| Roles | admin vs observer (read-only enforced server-side) |
| Agent link | random 24-byte token, WSS/TLS, TOFU cert pinning (fingerprint in agent config) |
| Laptop exposure | **zero inbound ports** — agent connects outbound only |
| Remote input | active only while mirroring; failsafe disables on errors/disconnect |
| USB | refuses to disable keyboard/mouse/HID class devices |
| Audit | every login, command, capture, error persisted in `data/hks.db` |
| Captures | login webcam/screenshot uploads stored server-side with timestamps |

## Know the limits (honest list)
* The panel is a single-process app meant for **one admin (plus optional
  observers)**, not a multi-tenant SaaS.
* Self-signed TLS + TOFU pinning is strong against passive MITM but the very
  first connection trusts the network. On first setup, verify the fingerprint
  shown in the agent log matches your server cert.
* The webcam capture uses the normal Windows camera stack — if Windows camera
  access is disabled in privacy settings, that capture reports an error
  (visible in the Errors section) instead of failing silently.
* This tool does **not** hide itself: `HKSAgent` is a normal, visible service
  and `uninstall.bat` removes it. That is deliberate — covert persistence on
  machines you don't own is illegal in most jurisdictions.

## Incident response
* Suspect the panel is compromised → revoke all sessions (Users → Sessions),
  change the admin password, rotate `HKS_AGENT_TOKEN`.
* Suspect the laptop is compromised → `sc stop HKSAgent` locally or
  `uninstall.bat`, then investigate.
* Panel database is `HKS_AdminPanel/data/` — back it up or wipe it as needed.

## Legal
Install only on hardware you own or are explicitly authorized to administer.
Unauthorized access to computer systems is a crime (IT Act 2000 §43/66,
CFAA, Computer Misuse Act, etc.).
