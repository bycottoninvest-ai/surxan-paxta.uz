@echo off
chcp 65001 >nul
title SURXON PAXTA - TEST
cd /d "%~dp0"
where python >nul 2>nul || (echo Python o'rnatilmagan. https://www.python.org/downloads/ dan o'rnating ^(Add to PATH belgisini qo'ying^). & pause & exit /b 1)
if not exist .venv (
  echo Birinchi ishga tushirish: kutubxonalar o'rnatilmoqda...
  python -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -r requirements.txt || (pause & exit /b 1)
)
echo Windows "Firewall" ruxsat so'rasa, telefondan kirish uchun "Private network" ga ruxsat bering.
.venv\Scripts\python tools\run_local.py %*
pause
