STATIC - THE WEB UI (NO BUILD CHAIN - VANILLA HTML/CSS/JS ON PURPOSE)
======================================================================
Kept framework-free so the host computer serves it with zero npm, zero
node_modules, one folder. The 2026 UI/UX report is implemented HERE.

  index.html ... login + app shell + STATUS STRIP
  app.js ....... all screens (29 sections, grouped sidebar MONITOR /
                 CONTROL / MANAGE / AUTOMATE / CONFIGURE), KPI cards with
                 sparklines, connection-quality indicator, loading/empty/
                 error states, density toggle
  style.css .... design tokens (layered dark surfaces) + components
  screen.html .. the phone-friendly screen view
  setup.html ... first-run admin account page
Cache is busted with ?v=N - bump BOTH links in index.html when changed.

