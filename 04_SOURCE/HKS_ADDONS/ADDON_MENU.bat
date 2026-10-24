@echo off
REM ============================================================
REM  HKS ADD-ONS MENU - install shared-repo services on Windows
REM  Run from the HKS extract folder (next to INSTALL_HKS.bat)
REM ============================================================
:menu
cls
echo  ============================================================
echo   HKS ADD-ONS  (37 kits from the shared repo list)
echo  ============================================================
echo   0) Prerequisites first (git + Node.js + Docker Desktop)
echo   1) MeshCentral         - full remote-management server (its o :8490
echo   2) ttyd                - terminal in the browser (shared-mach :7681
echo   3) filegator           - PHP web file manager (separate from  :8090
echo   4) code-server         - VS Code in the browser :8482
echo   5) DbGate              - database GUI in the browser :8485
echo   6) Shinobi             - CCTV NVR (installer moves its web po :8480
echo   7) Nextcloud AIO       - self-hosted cloud suite (masterconta :8443
echo   8) CasaOS              - home-server dashboard inside WSL2 Ub :8080
echo   9) Mopidy + Iris       - music server + web UI (sound needs G :6680
echo  10) AnywhereInput       - the browser PC-control project (HKS  :8008
echo  11) filebrowser         - single-exe web file manager over C:\ :8487
echo  12) AudioRelay          - audio streaming to the phone (freewa (tool)
echo  13) RustDesk            - open-source remote desktop (own Team (tool)
echo  14) TigerVNC            - classic VNC server + viewer (GPL) (tool)
echo  15) Deskflow            - share one keyboard+mouse across PCs  :24800
echo  16) Tailscale           - WireGuard mesh VPN - reach the lapto (tool)
echo  17) ZeroTier            - mesh VPN - virtual LAN between your  :9993
echo  18) Netbird             - open-source WireGuard mesh VPN with  (tool)
echo  19) frp                 - self-hosted reverse proxy - expose H :7500
echo  20) Syncthing           - continuous file sync between your de :8384
echo  21) rclone              - copy/sync to 70+ cloud storages (web :5572
echo  22) Kopia               - encrypted backups to disk/cloud (web :51515
echo  23) windows_exporter    - Prometheus metrics endpoint for Wind :9182
echo  24) Glances             - all-in-one system monitor in the bro :61208
echo  25) Netdata             - real-time per-second monitoring dash :19999
echo  26) Uptime Kuma         - uptime/status monitoring with alerti :3001
echo  27) osquery             - query the OS like a SQL database (tool)
echo  28) Velociraptor        - endpoint visibility / DFIR console ( :8889
echo  29) Nmap                - network + port scanner (Npcap for fu (tool)
echo  30) LibreHardwareMonitor  - CPU/GPU/disk temperatures + fan spee :8088
echo  31) FanControl          - custom fan curves - helps the 24/7 a (tool)
echo  32) ShareX              - screenshots / screen recording with  (tool)
echo  33) yt-dlp              - download videos/audio from the web ( (tool)
echo  34) Jellyfin            - free media server - movies/music to  :8096
echo  35) WinSW               - wrap ANY exe as a Windows service (h (tool)
echo  36) AutoHotkey          - Windows automation scripting (v2) (tool)
echo  37) OBS Studio          - record/stream the screen (complement (tool)
echo   q) Quit
echo.
set /p c=Choose a number: 
if "%c%"=="0" call "%~dp0ADDON_PREREQS.bat"
if "%c%"=="1" call "%~dp0ADDON_MESHCENTRAL.bat" & goto menu
if "%c%"=="2" call "%~dp0ADDON_TTYD.bat" & goto menu
if "%c%"=="3" call "%~dp0ADDON_FILEGATOR.bat" & goto menu
if "%c%"=="4" call "%~dp0ADDON_CODESERVER.bat" & goto menu
if "%c%"=="5" call "%~dp0ADDON_DBGATE.bat" & goto menu
if "%c%"=="6" call "%~dp0ADDON_SHINOBI.bat" & goto menu
if "%c%"=="7" call "%~dp0ADDON_NEXTCLOUD.bat" & goto menu
if "%c%"=="8" call "%~dp0ADDON_CASAOS.bat" & goto menu
if "%c%"=="9" call "%~dp0ADDON_MOPIDY.bat" & goto menu
if "%c%"=="10" call "%~dp0ADDON_ANYWHEREINPUT.bat" & goto menu
if "%c%"=="11" call "%~dp0ADDON_FILEBROWSER.bat" & goto menu
if "%c%"=="12" call "%~dp0ADDON_AUDIORELAY.bat" & goto menu
if "%c%"=="13" call "%~dp0ADDON_RUSTDESK.bat" & goto menu
if "%c%"=="14" call "%~dp0ADDON_TIGERVNC.bat" & goto menu
if "%c%"=="15" call "%~dp0ADDON_DESKFLOW.bat" & goto menu
if "%c%"=="16" call "%~dp0ADDON_TAILSCALE.bat" & goto menu
if "%c%"=="17" call "%~dp0ADDON_ZEROTIER.bat" & goto menu
if "%c%"=="18" call "%~dp0ADDON_NETBIRD.bat" & goto menu
if "%c%"=="19" call "%~dp0ADDON_FRP.bat" & goto menu
if "%c%"=="20" call "%~dp0ADDON_SYNCTHING.bat" & goto menu
if "%c%"=="21" call "%~dp0ADDON_RCLONE.bat" & goto menu
if "%c%"=="22" call "%~dp0ADDON_KOPIA.bat" & goto menu
if "%c%"=="23" call "%~dp0ADDON_WINDOWSEXPORTER.bat" & goto menu
if "%c%"=="24" call "%~dp0ADDON_GLANCES.bat" & goto menu
if "%c%"=="25" call "%~dp0ADDON_NETDATA.bat" & goto menu
if "%c%"=="26" call "%~dp0ADDON_UPTIMEKUMA.bat" & goto menu
if "%c%"=="27" call "%~dp0ADDON_OSQUERY.bat" & goto menu
if "%c%"=="28" call "%~dp0ADDON_VELOCIRAPTOR.bat" & goto menu
if "%c%"=="29" call "%~dp0ADDON_NMAP.bat" & goto menu
if "%c%"=="30" call "%~dp0ADDON_LIBREHWMONITOR.bat" & goto menu
if "%c%"=="31" call "%~dp0ADDON_FANCONTROL.bat" & goto menu
if "%c%"=="32" call "%~dp0ADDON_SHAREX.bat" & goto menu
if "%c%"=="33" call "%~dp0ADDON_YTDLP.bat" & goto menu
if "%c%"=="34" call "%~dp0ADDON_JELLYFIN.bat" & goto menu
if "%c%"=="35" call "%~dp0ADDON_WINSW.bat" & goto menu
if "%c%"=="36" call "%~dp0ADDON_AUTOHOTKEY.bat" & goto menu
if "%c%"=="37" call "%~dp0ADDON_OBS.bat" & goto menu
if /i "%c%"=="q" exit /b 0
goto menu
