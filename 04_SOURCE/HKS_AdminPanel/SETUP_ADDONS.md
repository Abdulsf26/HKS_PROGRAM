# HKS ADD-ONS — install the shared repos for real (terminal + .bat)

This is the "download and set up everything" kit. Every repo you shared that can
actually run on Windows has an installer in the `HKS_ADDONS` folder of the zip.
Each installer: self-elevates -> checks/installs its runtime (winget: git,
Node.js, Docker Desktop; pip; PHP; GitHub downloads) -> downloads the project
(npm / GitHub release API / docker pull / git raw) -> installs it under
`C:\HKS_PROGRAM\addons\<name>` -> opens its LAN port in the firewall -> starts
it -> prints the URL.

## How to run (on the laptop)
1. Extract `HKS_PROGRAM.zip` anywhere.
2. Double-click `HKS_ADDONS\ADDON_PREREQS.bat` (one time; reboot if it installed
   Node/Docker).
3. Double-click `HKS_ADDONS\ADDON_MENU.bat` and pick a number — or run any
   `ADDON_*.bat` directly.
4. Watch the panel: **Settings → Open source → "Add-on services on the laptop"**
   turns 🟢 with an **Open** button when a service is live (the board asks the
   agent to probe the ports; URLs use the laptop's LAN IP, so your phone can
   open them too).

## What each installer does
| Add-on | Runtime | Source | Port |
|---|---|---|---|
| MeshCentral | Node.js (winget) | `npm install meshcentral`, `settings.json` pinned to 8490 | 8490 |
| ttyd | none | GitHub release exe (1.7.7, falls back 1.7.3) | 7681 |
| filegator | PHP 8.3 nts (windows.php.net latest zip) | GitHub latest-release API | 8090 |
| code-server | Docker | `codercom/code-server` image, volume `hks_code_data` | 8482 |
| DbGate | Docker | `dbgate/dbgate` image | 8485 |
| Shinobi | Docker | official `docker-compose.yml` (host port moved to 8480) | 8480 |
| Nextcloud AIO | Docker | official mastercontainer run command | 80/8080/8443 |
| CasaOS | WSL2 Ubuntu | `wsl --install -d Ubuntu` + official install.sh | 8080 |
| Mopidy + Iris | pip | `pip install mopidy mopidy-iris` | 6680 |
| AnywhereInput | pip | `pip install anywhereinput`, `--tunnel local` | 8008 |

## Honest notes (read once)
- **Docker add-ons** need Docker Desktop running and a reboot after first
  install. Each container takes RAM — run only the services you need to limit resource use.
- **Port plan avoids clashes**: Shinobi was moved to 8480; Nextcloud AIO still
  wants 80/8080/8443 (stop CasaOS during its setup).
- **Default credentials**: filegator `admin/admin123` — change on first login.
  MeshCentral creates its admin on first open. ttyd has NO login (LAN only!).
- **Mopidy sound** needs GStreamer (not auto-installed on Windows) — the web UI
  works; this is why it is marked experimental.
- **AnywhereInput** is the project that taught us the DPI lesson; HKS already
  covers its features natively — it is installed only for comparison.
- Removal: delete `C:\HKS_PROGRAM\addons\<name>` + remove the `HKS <name>`
  firewall rule (or `docker rm -f hks-...` for containers).

