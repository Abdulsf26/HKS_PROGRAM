@echo off
title HKS Add-on - yt-dlp
echo === yt-dlp - download videos/audio (CLI) ===
set D=%ProgramData%\HKS\ytdlp
mkdir "%D%" 2>nul
cd /d "%D%"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $r=Invoke-RestMethod 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'; $a=@($r.assets)|Where-Object{$_.name -match '^yt-dlp\.exe$'}|Select-Object -First 1; if(-not $a){throw 'no matching release asset'}; Invoke-WebRequest -UseBasicParsing $a.browser_download_url -OutFile 'yt-dlp.exe'"
if not exist yt-dlp.exe (
  echo [X] download failed for yt-dlp - run again / check github releases.
  pause
  exit /b 1
)
echo example: yt-dlp.exe -f mp4 URL
yt-dlp.exe --version
pause
