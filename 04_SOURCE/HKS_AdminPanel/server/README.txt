SERVER - THE PANEL BACKEND (python + fastapi)
==============================================
  main.py ......... ALL routes: login, REST API, /ws/dash + /ws/agent
                    relays, security gates (CSRF, roles, origin, bans).
                    VERSION string at the top = the build number.
  db.py ........... sqlite data layer (parameterized SQL only)
  security.py ..... password hashing (PBKDF2 600k), tokens, limiters
  static/ ......... the web UI (index.html + app.js + style.css + screen)
