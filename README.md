# HKS Program

<p align="center">
  <img alt="HKS" src="https://img.shields.io/badge/HKS-Windows%20Control-ffd600?style=for-the-badge&labelColor=111111">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-222222?style=for-the-badge">
  <img alt="UI" src="https://img.shields.io/badge/UI-Neo--brutalist-ff7043?style=for-the-badge&labelColor=111111">
</p>

**A self-hosted Windows dashboard and agent for managing a computer you own.** A local web panel talks to a Windows agent; use the browser from the laptop or a trusted device on your network.

<p align="center">
  <a href="demo.html">Open the standalone UI preview</a>
  <br><sub>Static sample values · no live device connection</sub>
</p>

## What’s inside

| Dashboard | Windows agent | Optional add-ons |
|---|---|---|
| Live status, files, terminal, media, logs and settings | Hardware, desktop-session and device features | Separate installers with independent requirements |

## Quick start

1. Read [Setup](02_DOCS/SETUP_GUIDE.md) and [Security](02_DOCS/SECURITY.md).
2. On Windows, run `00_START_HERE/HKS_SETUP.py` for the GUI path, or `00_START_HERE/INSTALL_HKS.bat` as Administrator.
3. Create your own admin credentials, then open the address shown by setup.

## Windows installer

[**Download HKS_PROGRAM_SETUP.exe**](HKS_PROGRAM_SETUP.exe) · Windows 10/11 · GUI installer · 13.4 MB

The installer contains the matching HKS 7.2.99 package. To rebuild the portable ZIP and installer from source, follow [the build tools](03_TESTS/README.txt).

## Developer quick checks

```powershell
py -m pytest .\04_SOURCE\HKS_AdminPanel\tests -q
node --check .\04_SOURCE\HKS_AdminPanel\server\static\app.js
node .\04_SOURCE\HKS_AdminPanel\tests\ui_smoke.js
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. Optional installers can change system services and firewall rules; inspect them and test on a disposable machine first.

## Project links

- [Feature overview](02_DOCS/FEATURES.md) · [Add-on guidance](02_DOCS/SETUP_ADDONS.md) · [Demo page](demo.html)
- [GitHub profile](https://github.com/Abdulsf26) · [Contact](mailto:fakmail112233@gmail.com)

## License

The original HKS code is licensed under the [MIT License](LICENSE). Third-party components and referenced projects retain their own terms; see [CREDITS](02_DOCS/CREDITS.md).
