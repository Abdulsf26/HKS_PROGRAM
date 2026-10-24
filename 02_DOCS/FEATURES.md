# HKS Feature List (70+)

All features are reachable from the 20-section web dashboard (desktop or phone browser).

## Access & security
1. Browser login (username + password, PBKDF2-hashed)
2. First-run setup wizard (/setup) creates the admin account
3. Per-IP login rate limiting (6/5 min) with automatic 15-min ban
4. Global API rate limiting (300 req/min per IP)
5. User management: add / change password / change role / delete
6. Roles: admin (full) vs observer (read-only)
7. Active session list + force-revoke; banned-IP list + unban
8. CSRF-protected mutations; HttpOnly session cookies
9. Dashboard lock screen
10. Complete audit log of every panel action
11. Agent token auth + TLS with TOFU certificate pinning
12. **Terminate everything**: client self-uninstall and panel self-destruct, both gated behind typing CONFIRM
13. **Client-side local panel** on the laptop (http://127.0.0.1:8590, loopback only): live status + UAC-elevated uninstall

## Live control
14. Full-screen mirroring (JPEG, adjustable fps/quality/monitor/scale)
15. Remote mouse control with failsafe auto-disable
16. **Remote keyboard**: type any text into the laptop + one-tap keys (Win, Win+D, Win+L, Alt+Tab, Alt+F4, Ctrl+C/V, Enter, Esc…)
17. Remote screenshot; per-frame PNG save
18. Terminal — admin (SYSTEM) and normal-user modes, PowerShell/CMD, command history
19. Windows OpenSSH server enable/disable/status
20. Launch any application remotely
21. Process list (CPU/RAM) + kill / suspend / resume
22. Startup (autorun) items list/remove
23. Window list + foreground app + user idle time

## Files & data
24. File manager over all drives — hidden & system files included
25. Upload / download (chunked), rename, delete, new folder
26. **Multi-select files → one-click ZIP download** (server streams the zip, laptop copy auto-deleted)
27. Drive-wide file search (bounded scan)
28. Media finder: images / videos / documents indexed separately across C:\ (cached, paginated)
29. **One-click "Download all as ZIP"** per media category (300 files / 1 GB cap, skip report)
30. Image thumbnails; play on laptop; delete; download
31. SQL access — SQLite files and MySQL/AWS RDS (query + table list)
32. Deploy any folder as a web server on chosen IP:port (start/stop/list)
33. **CSV export** of events / audit / telemetry from the panel

## Sensors & audio
34. Live camera streaming (multi-camera detect) + snapshots
35. Microphone clip recording (playback in browser)
36. **Two-way live audio**: listen through the laptop's mic AND talk through its speakers (16 kHz PCM over the agent link)
37. Login capture: webcam photo + screenshot + username on every laptop logon / unlock / resume
38. Remote clipboard read/write
39. Toast notifications + TTS speech to the laptop

## Device & power
40. Network adapters list + enable/disable (master internet switch)
41. Wi-Fi scan, saved profiles, connect (with password) / disconnect
42. Internet status + latency; **data usage since boot**
43. USB device list + per-device enable/disable (keyboard/mouse protected)
44. USB mass-storage kill switch; USB selective-suspend off (anti-heat)
45. Battery + power status; safeboot-pending indicator
46. Sleep, hibernate, display off, lock PC, **log off user**
47. Restart / timed shutdown + cancel
48. Sleep with wake signal (fixed time or "sleep for N minutes", Windows wake-to-run)
49. **Safe Mode: boot into it on next restart, and return to normal boot** (networking variant included)
50. Wake-timer listing; agent reconnects and reports on any wake
51. Scheduler: once / after N minutes / daily / interval tasks (commands, URLs, media, messages)
52. **Code scheduler**: upload Python/PowerShell/batch scripts (≤512 KB) and run them at a time, after a delay, or on a schedule — with run logs you can open in the panel
53. Windows schtasks bridge (list/run/delete)

## Monitoring & reliability
54. Live telemetry: CPU, RAM, battery, net, SSID, IP (5 s, adaptive)
55. **Deep hardware status**: CPU temperature, per-core load, clock speed, GPU (name/status/driver/VRAM, live util for NVIDIA), battery health/wear %, disk space bars, top memory consumers
56. **Hardware timeline**: automatic low-rate snapshots (~2 min) with temp/latency, shown as a table
57. **Threshold alerts**: low battery, low disk, RAM pressure — rate-limited, shown as toasts
58. **Boot banner**: "HKS PROGRAM HAS BEEN STARTED — starting up (~1 minute)" on every laptop boot
59. Event log: logons, logoffs, boot, shutdown, sleep, resume, network/USB changes, zip/script/terminate events
60. Windows security/system event queries (4624/4634/1074/6006/6008/42/107…)
61. Errors section: every agent failure with module + stack, resolvable
62. SYSTEM service: auto-start at boot, restart-on-failure (5s/10s/30s), supervising loop, infinite reconnect with backoff, offline event queue flushed on reconnect, self-destruct kill-switch file
63. **Connection latency** (agent↔server RTT) shown in Settings and hardware timeline
64. Resource optimization: BELOW_NORMAL priority, adaptive frame skip on CPU load, bounded queues, capped workers, rotating logs, DB auto-prune — designed for continuous use on resource-constrained Windows systems

## Interface
65. **Clean flat UI** (dark + light theme, no glassmorphism), consistent button system
66. **Mobile-first responsive**: hamburger nav, touch-sized controls, horizontal-scroll tables — full control from a phone browser
67. Live toasts + optional **alert sounds** on connect/disconnect/alerts
68. Empty/loading states everywhere; every button wired to a real action with error reporting
69. Structured tables/cards for all data — no raw dumps (pretty event formatting, meta decoding)
70. Cross-subnet ready: agent connects OUT to the panel, so any routed network / port-forward / VPN / public VPS works

