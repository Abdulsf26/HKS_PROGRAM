MODULES - SERVICE-CONTEXT ABILITIES (available before login)
==============================================================
One file per ability, each registers commands: addons, common, files,
network_ctl, power_ctl, apps, server_mgmt, shell, terminal, webdeploy,
watchdog, win_updates, usb_guard...
Rule: every command returns a result or a CLEAR error - never hangs
(the hub has bounded queues + timeouts).
