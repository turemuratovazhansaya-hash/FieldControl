# -*- coding: utf-8 -*-
"""
check_ai.py — быстрая проверка подключения ИИ без запуска сайта.

    python check_ai.py

Показывает, какой провайдер включён, отправляет тестовый вопрос и печатает
ответ или точную причину ошибки (неверный ключ, лимит, недоступна модель...).
Для Gemini additionally выводит список доступных вашему ключу моделей.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()
import ai_service  # noqa: E402

info = ai_service.status()
print("Провайдер:", info["provider"], "| модель:", info["model"])

if not info["enabled"]:
    print("\nИИ выключен: в файле .env не найден ключ выбранного провайдера.")
    print("Проверьте, что файл называется именно .env, лежит рядом с main.py,")
    print("и в нём есть строка вида GEMINI_API_KEY=ваш_ключ (без пробелов и кавычек).")
    raise SystemExit(1)

# --- ИЗМЕНЕНИЕ: Проверка для Gemini ---
if info["provider"] == "gemini":
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": key}, params={"pageSize": 100}, timeout=20)
        r.raise_for_status()
        names = [m["name"].replace("models/", "") for m in r.json().get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", [])]
        flash = [
            n for n in names
            if "flash" in n and "tts" not in n and "image" not in n and "omni" not in n
        ]
        print("\nКлюч принят. Текстовые Flash-модели:", ", ".join(flash[:12]) or "не найдено")
    except Exception as exc:
        detail = getattr(getattr(exc, "response", None), "text", "")[:300]
        print("\nНе удалось получить список моделей:", exc, detail)
# --------------------------------------------

print("\nОтправляю тестовый вопрос...")
text, provider = ai_service.ask("Ответь одним коротким предложением: как тебя зовут и чем ты можешь помочь торговому агенту?")
if text:
    print("\nУСПЕХ (" + provider + "):\n" + text)
else:
    print("\nОтвета нет — причина выведена выше строкой [AI ERROR].")
    raise SystemExit(1)
