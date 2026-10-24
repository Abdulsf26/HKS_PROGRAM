# HKS Setup Guide

HKS runs a web dashboard and agent on a Windows computer; authorized devices use a browser to reach the dashboard.

## Install

1. Extract the release archive, or run the GUI setup executable.
2. Start `INSTALL_HKS.bat` as Administrator if using the archive.
3. Complete the setup wizard, choose the port and create the administrator credentials.
4. Open the address printed by setup. Use HTTPS for browser microphone features.
5. Keep the service on a trusted network and follow `SECURITY.md` before enabling remote access.

## Maintenance

- **Status:** use `CHECK_STATUS.bat` or the setup GUI.
- **Diagnostics:** use `HKS_DOCTOR.bat` or the setup GUI.
- **Startup repair:** run `HKS_REPAIR_AUTOSTART.ps1` as Administrator if boot tasks are missing.
- **Update:** run the new setup package and choose Install / Repair.
- **Uninstall:** use the GUI uninstall flow and review the backup choice.

## Troubleshooting

| Symptom | Next step |
|---|---|
| Browser cannot connect | Confirm the dashboard address and port, same-network access, and Windows Firewall policy. |
| Service agent offline | Run status and diagnostics; inspect logs in `C:\\HKS_PROGRAM\\data`. |
| Desktop controls unavailable | Sign in to the Windows desktop; screen, camera, and input features require an interactive session. |
| Startup missing | Run `C:\\HKS_PROGRAM\\HKS_REPAIR_AUTOSTART.ps1` as Administrator and inspect `data\\autostart_repair.log`. |

Add-ons have separate dependencies and are not required for the core panel and agent.
