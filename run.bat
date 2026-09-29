@echo off
title Ranobes Scraper

:: Batch fayl qayerda bo'lsa o'sha papkaga o'tish
:: (administrator rejimida ham ishlaydi)
cd /d "%~dp0"

echo ========================================
echo   Ranobes Scraper
echo ========================================
echo.
echo Papka: %~dp0
echo.

:: Python bor-yo'qligini tekshirish
python --version >nul 2>&1
if errorlevel 1 (
    echo [XATO] Python topilmadi!
    echo.
    echo Python yuklab oling: https://www.python.org/downloads/
    echo O'rnatishda "Add Python to PATH" ni belgilang!
    echo.
    pause
    exit /b 1
)

echo Python versiyasi:
python --version
echo.

:: requirements.txt bor-yo'qligini tekshirish
if not exist "%~dp0requirements.txt" (
    echo [XATO] requirements.txt topilmadi!
    echo Barcha fayllar bir papkada bolishi kerak:
    echo   - ranobes_scraper.py
    echo   - requirements.txt
    echo   - run.bat
    echo.
    pause
    exit /b 1
)

echo [1/2] Kerakli kutubxonalar ornatilmoqda...
pip install -r "%~dp0requirements.txt" --quiet --break-system-packages
if errorlevel 1 (
    echo [OGOHLANTIRISH] Kutubxona ornatishda muammo boldi, davom etilmoqda...
)
echo.

:: ranobes_scraper.py bor-yo'qligini tekshirish
if not exist "%~dp0ranobes_scraper.py" (
    echo [XATO] ranobes_scraper.py topilmadi!
    echo Barcha fayllar bir papkada bolishi kerak.
    echo.
    pause
    exit /b 1
)

echo [2/2] Dastur ishga tushirilmoqda...
echo.
python "%~dp0ranobes_scraper.py"

echo.
echo Dastur yopildi.
pause
