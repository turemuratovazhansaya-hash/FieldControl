@echo off
chcp 65001 >nul
cd /d "%~dp0"
title MilkControl Pro

echo ===== MilkControl Pro: настройка и запуск =====

if not exist venv (
  echo Создаю виртуальное окружение...
  python -m venv venv
  if errorlevel 1 (
    echo Не удалось запустить Python. Проверьте, что Python установлен и добавлен в PATH.
    pause
    exit /b 1
  )
)
call venv\Scripts\activate

echo Устанавливаю зависимости...
pip install -q -r requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости. Проверьте интернет и повторите запуск.
  pause
  exit /b 1
)

findstr /r /c:"^GEMINI_API_KEY=..*" .env >nul 2>&1
if errorlevel 1 (
  echo.
  echo Ключ Gemini не указан. Сейчас откроется файл .env:
  echo вставьте ключ после GEMINI_API_KEY= ^(без пробелов и кавычек^), сохраните и закройте блокнот.
  echo Ключ берётся здесь: https://aistudio.google.com/apikey
  notepad .env
)

echo.
echo ===== Проверка ИИ =====
python check_ai.py

echo.
echo ===== Запуск сервера =====
echo Сайт: http://localhost:5000   Руководитель: http://localhost:5000/dashboard
echo Остановить сервер: Ctrl+C
python main.py
pause
