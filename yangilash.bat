@echo off
chcp 65001 >nul
title SURXON PAXTA - yangilash
rem Run from a temporary copy: this file itself gets replaced during the update,
rem and cmd.exe would otherwise continue reading the new file at the wrong position.
if not "%~1"=="--run" (
  copy /y "%~f0" "%TEMP%\surxon_yangilash.bat" >nul
  "%TEMP%\surxon_yangilash.bat" --run "%~dp0"
)
cd /d "%~2"
echo.
echo  GitHub'dan eng yangi versiya yuklanmoqda...
echo  (data-test va .venv papkalari saqlanadi, ular o'chirilmaydi)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$z=Join-Path $env:TEMP 'surxon_yangi.zip'; $d=Join-Path $env:TEMP 'surxon_yangi';" ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;" ^
  "Invoke-WebRequest 'https://github.com/bycottoninvest-ai/surxan-paxta.uz/archive/refs/heads/main.zip' -OutFile $z -UseBasicParsing;" ^
  "if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Expand-Archive $z $d -Force;" ^
  "$src = Join-Path $d 'surxan-paxta.uz-main';" ^
  "robocopy $src '%CD%' /E /XD data-test .venv data /NFL /NDL /NJH /NJS /NP | Out-Null;" ^
  "if ($LASTEXITCODE -ge 8) { throw 'Fayllarni nusxalab bolmadi' };" ^
  "Remove-Item $z, $d -Recurse -Force; Write-Host ' Fayllar yangilandi.'"
if errorlevel 1 (
  echo.
  echo  Yangilab bo'lmadi. Internetni tekshirib, qayta urinib ko'ring.
  pause & exit /b 1
)
if exist .venv\Scripts\python.exe (
  echo  Kutubxonalar tekshirilmoqda...
  .venv\Scripts\python -m pip install --quiet -r requirements.txt
)
echo.
for /f "tokens=3 delims= '" %%v in ('findstr /b "VERSION" surxon\__init__.py') do echo  Yangi versiya: v%%v
echo  Tayyor. Endi start_test ni ishga tushiring.
echo.
pause
