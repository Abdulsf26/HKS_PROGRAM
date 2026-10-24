# Agent shows OFFLINE

1. On the laptop run CHECK_STATUS.bat - it tells you which piece is down.
2. Reboot the laptop once. The installer's tasks start the panel and agents again.
3. Still offline? Run INSTALL_HKS.bat again as Administrator (it repairs in place) - answer N at "Use HTTPS?".
4. The panel itself open but the agent red? The agent task is separate - HKS_DOCTOR.bat names it.
