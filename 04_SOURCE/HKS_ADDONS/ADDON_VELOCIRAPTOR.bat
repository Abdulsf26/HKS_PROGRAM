@echo off
title HKS Add-on - Velociraptor
echo === Velociraptor - endpoint visibility / DFIR console ===
set D=%ProgramData%\HKS\velociraptor
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/Velocidex/velociraptor/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match 'windows-amd64\.exe\.gz$'}|Select-Object -First 1; if(-not $a){throw 'no matching Windows amd64 release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'velociraptor.exe.gz'; $src=[IO.File]::OpenRead('velociraptor.exe.gz'); try { $gz=[IO.Compression.GZipStream]::new($src,[IO.Compression.CompressionMode]::Decompress); try { $dst=[IO.File]::Create('velociraptor.exe'); try { $gz.CopyTo($dst) } finally { $dst.Dispose() } } finally { $gz.Dispose() } } finally { $src.Dispose() }; Remove-Item 'velociraptor.exe.gz' -Force"
if not exist velociraptor.exe (
  echo [X] download failed for Velociraptor - run again / check github releases.
  pause
  exit /b 1
)
echo console: https://LOCALIP:8889 (self-signed - accept the warning)
powershell -NoProfile -Command "if (-not (Get-NetFirewallRule -DisplayName 'HKS velociraptor' -ErrorAction SilentlyContinue)) { New-NetFirewallRule -DisplayName 'HKS velociraptor' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8889 | Out-Null }"
velociraptor.exe gui
