# HKS Client (Windows agent)

The HKS Client runs on a Windows computer you own or are authorized to administer. It connects to an HKS Admin Panel and provides the platform-specific system and interactive-session features enabled by your configuration.

## Setup

For the bundled product, use the GUI setup application or `INSTALL_HKS.bat` in `00_START_HERE`. For a standalone client deployment, review `setup.bat` and `config.json` handling before use; provide the panel URL and a unique agent token through the supported setup flow. Do not commit live configuration or credentials.

## Runtime requirements

- Windows 10/11 or a compatible Windows Server release.
- Python and the packages listed in `requirements.txt` when using the source install path.
- Administrator rights for machine-wide service and firewall configuration.
- An interactive signed-in Windows session for screen, input, camera, and microphone functions.

Refer to the product setup and security guides for network and recovery guidance. Optional hardware features depend on device availability and Windows privacy settings.
