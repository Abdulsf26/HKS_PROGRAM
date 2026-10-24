AGENT - THE LAPTOP SIDE (runs as Windows service + desktop session)
=====================================================================
  main.py ........... service agent entry (SYSTEM context, pre-logon OK)
  session_main.py ... desktop session agent (your logged-in Windows user;
                      preflight exits 42/43 instead of crash-looping)
  connection.py ..... the ONE outbound link: reconnect forever, backoff
                      capped at 30s + jitter, TLS pin fail-CLOSED
  common.py ......... shared Ctx: queues, watchdogs, hooks
  janitor.py ........ local cleanup + old-file sweep
  win_service.py .... service plumbing (plain tasks, no pywin32)
  local_panel.py .... tiny localhost-only helper page
  modules/ .......... service-context abilities
  session_modules/ .. desktop-only abilities (screen, input, camera...)
