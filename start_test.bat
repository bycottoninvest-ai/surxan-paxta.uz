@echo off
chcp 65001 >nul
title SURXON PAXTA - TEST
cd /d "%~dp0"
rem Find a real Python (the Microsoft Store "python" alias only prints "Python" and exits).
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
  echo.
  echo  Python o'rnatilmagan yoki PATH ga qo'shilmagan.
  echo  1^) python.org dan "Windows installer ^(64-bit^)" ni yuklab oching
  echo  2^) Birinchi oynada "Add python.exe to PATH" belgisini qo'ying, "Install Now"
  echo  3^) Keyin start_test ni qayta ishga tushiring.
  echo.
  pause & exit /b 1
)
if not exist .venv\Scripts\python.exe (
  if exist .venv rmdir /s /q .venv
  echo Birinchi ishga tushirish: kutubxonalar o'rnatilmoqda ^(1-3 daqiqa^)...
  %PY% -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt || (echo Kutubxonalarni o'rnatib bo'lmadi. Internetni tekshiring. & pause & exit /b 1)
)
echo Windows "Firewall" ruxsat so'rasa, telefondan kirish uchun "Private network" ga ruxsat bering.
.venv\Scripts\python tools\run_local.py %*
pause
