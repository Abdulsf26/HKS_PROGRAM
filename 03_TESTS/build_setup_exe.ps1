param(
  [string]$Python = "C:\HKS_PROGRAM\venv\Scripts\python.exe"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$archive = Join-Path $root "HKS_PROGRAM.zip"
$script = Join-Path $root "00_START_HERE\HKS_SETUP.py"
$out = Join-Path $root "HKS_PROGRAM_SETUP.exe"
$work = Join-Path $env:TEMP "hks-setup-pyinstaller"
if (-not (Test-Path -LiteralPath $archive)) { throw "Build HKS_PROGRAM.zip first: $archive" }
if (-not (Test-Path -LiteralPath $script)) { throw "Setup launcher source missing: $script" }
& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
  throw "PyInstaller is not installed in $Python. Install it in an isolated build environment, then rerun this script."
}
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name HKS_PROGRAM_SETUP --distpath $root --workpath $work `
  --specpath $work --add-data "$archive;." $script
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $out)) { throw "PyInstaller reported success but did not create $out" }
$exe = Get-Item -LiteralPath $out
if ($exe.Length -lt 1000000) { throw "Setup executable is unexpectedly small ($($exe.Length) bytes)." }
& $out --verify-package
if ($LASTEXITCODE -ne 0) { throw "The built EXE could not verify its embedded HKS package." }
Get-FileHash -Algorithm SHA256 -LiteralPath $out | Format-List Algorithm,Hash,Path
Write-Output "Created and payload-verified $out ($($exe.Length) bytes). The installer has not been run."
