@echo off
title CF Proxy — Cloudflare Bypass
cd /d "%~dp0"

echo ========================================
echo   CF Proxy — Cloudflare Bypass Server
echo ========================================
echo.
echo Bu dastur ranobes_scraper.py bilan
echo birgalikda ishlaydi.
echo.
echo Ishlatish tartibi:
echo   1. Ushbu oynani ochiq qoldiring
echo   2. Alohida oynada ranobes_scraper.py ni ishga tushiring
echo   3. Scraper avtomatik bu proxy ni ishlatadi
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [XATO] Python topilmadi!
    pause
    exit /b 1
)

echo [1/2] playwright o'rnatilmoqda...
pip install playwright --quiet --break-system-packages
playwright install chromium
echo.

echo [2/2] CF Proxy ishga tushirilmoqda...
echo.
python "%~dp0cf_proxy.py"

echo.
echo CF Proxy yopildi.
pause
