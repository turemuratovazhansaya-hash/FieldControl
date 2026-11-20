# -*- coding: utf-8 -*-
"""
ai_service.py — единая точка подключения ИИ для MilkControl Pro.

Поддерживаемые провайдеры (выбираются автоматически по ключам в .env
или принудительно через AI_PROVIDER):

    gemini     — Google Gemini (ключ из Google AI Studio, aistudio.google.com/apikey)
                 нужны: GEMINI_API_KEY (или GOOGLE_API_KEY), (опц.) GEMINI_MODEL
    openai     — OpenAI и ЛЮБОЙ OpenAI-совместимый API (OpenRouter, Groq, DeepSeek, Together и т.д.)
                 нужны: OPENAI_API_KEY, (опц.) OPENAI_BASE_URL, OPENAI_MODEL
    anthropic  — Claude API
                 нужны: ANTHROPIC_API_KEY, (опц.) ANTHROPIC_MODEL
    yandex     — YandexGPT
                 нужны: YANDEX_API_KEY, YANDEX_FOLDER_ID, (опц.) YANDEX_MODEL

Если ключей нет или API недоступен — функции возвращают (None, None),
и бэкенд автоматически использует готовые правила (демо не ломается).
"""

import os

import requests

TIMEOUT = 35  # секунд на ответ ИИ

# Актуальные текстовые модели. gemini-2.5-flash новым ключам уже недоступна,
# а gemini-flash-latest часто отвечает 503. Берём первую, которая реально отвечает.
GEMINI_MODELS = (
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
)
GEMINI_RETRY_STATUS = {404, 408, 429, 500, 502, 503, 504}

SYSTEM_PROMPT = (
    "Ты — ИИ-консультант системы MilkControl Pro для молочной компании. "
    "Ты помогаешь торговым представителям и руководителю отдела продаж. "
    "Отвечай ТОЛЬКО на русском языке, кратко, конкретно и по делу: "
    "используй нумерованные шаги и короткие абзацы, выделяй важное через **жирный**. "
    "Если просят скрипт — давай готовый текст реплик для разговора с владелецей торговой точки. "
    "Если спрашивают про падение продаж, морозильное оборудование или невыполнение плана — "
    "опирайся на данные из блока «Данные системы», если они есть, и не выдумывай цифры, "
    "которых там нет. Не пиши длиннее 1500 символов."
)


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _has_key(provider):
    if provider == "openai":
        return bool(_env("OPENAI_API_KEY"))
    if provider == "anthropic":
        return bool(_env("ANTHROPIC_API_KEY"))
    if provider == "gemini":
        return bool(_env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"))
    if provider == "yandex":
        return bool(_env("YANDEX_API_KEY") and _env("YANDEX_FOLDER_ID"))
    return False


def get_provider():
    """Определяет активного провайдера: gemini | openai | anthropic | yandex | none."""
    forced = _env("AI_PROVIDER").lower()
    if forced in ("none", "off", "rules"):
        return "none"
    if forced in ("openai", "gemini", "anthropic", "yandex"):
        return forced if _has_key(forced) else "none"   # ключ не задан — ИИ выключен

    # --- ИЗМЕНЕНИЕ: Проверяем Gemini ПЕРВЫМ ---
    # Приоритет: Gemini -> OpenAI -> Anthropic -> Yandex
    for provider in ("gemini", "openai", "anthropic", "yandex"):
        if _has_key(provider):
            return provider
    return "none"
# --------------------------------------------


def status():
    """Информация для /api/ai/status и /api/health."""
    provider = get_provider()
    model = {
        "openai": _env("OPENAI_MODEL", "gpt-4o-mini"),
        "gemini": _env("GEMINI_MODEL", GEMINI_MODELS[0]),
        "anthropic": _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        "yandex": _env("YANDEX_MODEL", "yandexgpt-lite"),
    }.get(provider)
    return {"provider": provider, "enabled": provider != "none", "model": model}


def _clean_history(history, message):
    """
    Превращает историю чата в корректный список сообщений:
    только роли user/assistant, первое сообщение — user, роли чередуются,
    последнее — текущее сообщение пользователя.
    """
    items = []
    if isinstance(history, list):
        for item in history[-10:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            text = str(item.get("content") or item.get("text") or "").strip()
            if role in ("user", "assistant") and text:
                items.append({"role": role, "content": text})

    items.append({"role": "user", "content": message})

    merged = []
    for item in items:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n" + item["content"]
        else:
            merged.append(dict(item))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


# ---------------------------------------------------------------------------
# ВЫЗОВЫ ПРОВАЙДЕРОВ
# ---------------------------------------------------------------------------
def _call_openai(system, messages, max_tokens):
    base = _env("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    headers = {
        "Authorization": f"Bearer {_env('OPENAI_API_KEY')}",
        "Content-Type": "application/json",
    }

    def send(msgs):
        response = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={
                "model": _env("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": msgs,
                "temperature": 0.4,
                "max_tokens": max_tokens,
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()

    try:
        return send([{"role": "system", "content": system}] + messages)
    except requests.HTTPError as exc:
        # Часть бесплатных моделей (например, Gemma) не принимают роль system (ошибка 400).
        # Повторяем запрос, вложив инструкции в первое сообщение пользователя.
        if exc.response is None or exc.response.status_code != 400:
            raise
        merged = [dict(m) for m in messages]
        merged[0]["content"] = system + "\n\n---\n\n" + merged[0]["content"]
        return send(merged)


def _gemini_models():
    """Сначала модель из .env, затем запасные, без повторов."""
    configured = _env("GEMINI_MODEL")
    models = []
    for name in ([configured] if configured else []) + list(GEMINI_MODELS):
        if name and name not in models:
            models.append(name)
    return models


def _gemini_visible_text(data):
    """Текст ответа человеку. Внутренние thoughts в чат не попадают."""
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise RuntimeError("Gemini заблокировал запрос: " + str(feedback["blockReason"]))
    candidates = data.get("candidates") or []
    if not candidates:
        return "", "нет кандидата"
    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    chunks = []
    for part in parts:
        if part.get("thought"):
            continue
        text = (part.get("text") or "").strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks).strip(), candidate.get("finishReason") or "неизвестна"


def _call_gemini(system, messages, max_tokens):
    api_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    contents = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    last_error = None
    for model in _gemini_models():
        # LOW — короткий ответ в чат. Если модель не принимает thinkingConfig, повторяем без него.
        thinking_options = ({"thinkingConfig": {"thinkingLevel": "LOW"}}, {})
        for thinking in thinking_options:
            payload = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.4,
                    "maxOutputTokens": max(max_tokens, 2048),
                    **thinking,
                },
            }
            try:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=TIMEOUT,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                break

            if response.status_code == 400 and thinking and "thinking" in response.text.lower():
                last_error = requests.HTTPError(
                    "400: thinkingConfig не подошёл модели " + model, response=response
                )
                continue
            if response.status_code in GEMINI_RETRY_STATUS:
                last_error = requests.HTTPError(
                    "%s: модель недоступна: %s" % (response.status_code, model),
                    response=response,
                )
                break

            response.raise_for_status()
            text, finish = _gemini_visible_text(response.json())
            if text:
                return text
            last_error = RuntimeError("Gemini (%s) вернул пустой ответ (причина: %s)" % (model, finish))
            break
    raise last_error or RuntimeError("Gemini не ответил")


def _call_anthropic(system, messages, max_tokens):
    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": _env("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    blocks = response.json().get("content", [])
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def _call_yandex(system, messages, max_tokens):
    folder = _env("YANDEX_FOLDER_ID")
    payload_messages = [{"role": "system", "text": system}] + [
        {"role": m["role"], "text": m["content"]} for m in messages
    ]
    response = requests.post(
        "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
        headers={
            "Authorization": f"Api-Key {_env('YANDEX_API_KEY')}",
            "x-folder-id": folder,
            "Content-Type": "application/json",
        },
        json={
            "modelUri": f"gpt://{folder}/{_env('YANDEX_MODEL', 'yandexgpt-lite')}",
            "completionOptions": {
                "stream": False,
                "temperature": 0.4,
                "maxTokens": str(max_tokens),
            },
            "messages": payload_messages,
        },
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["result"]["alternatives"][0]["message"]["text"].strip()


def analyze_image(image_bytes, mime_type, prompt):
    """Analyze an uploaded image with Gemini Vision; return (text, provider)."""
    if get_provider() != "gemini":
        return None, None
    import base64

    encoded = base64.b64encode(image_bytes).decode("ascii")
    api_key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    last_error = None
    for model in _gemini_models():
        for thinking in ({"thinkingConfig": {"thinkingLevel": "LOW"}}, {}):
            payload = {
                "systemInstruction": {"parts": [{"text": "Ты анализируешь фото выкладки молочной продукции. Опиши только то, что видно на фото; не выдумывай товары и проценты заполненности. Кратко укажи состояние полок и конкретные замечания."}]},
                "contents": [{"role": "user", "parts": [
                    {"text": prompt},
                    {"inlineData": {"mimeType": mime_type, "data": encoded}},
                ]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500, **thinking},
            }
            try:
                response = requests.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload, timeout=TIMEOUT,
                )
                if response.status_code == 400 and thinking and "thinking" in response.text.lower():
                    continue
                if response.status_code in GEMINI_RETRY_STATUS:
                    last_error = requests.HTTPError(f"{response.status_code}: модель недоступна: {model}", response=response)
                    break
                response.raise_for_status()
                text, finish = _gemini_visible_text(response.json())
                if text:
                    return text, "gemini"
                last_error = RuntimeError(f"Gemini вернул пустой ответ ({finish})")
                break
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError, RuntimeError) as exc:
                last_error = exc
                break
    if last_error:
        print(f"[AI IMAGE ERROR] provider=gemini: {last_error}")
    return None, None


_CALLERS = {
    "openai": _call_openai,
    "gemini": _call_gemini,
    "anthropic": _call_anthropic,
    "yandex": _call_yandex,
}


# ---------------------------------------------------------------------------
# ПУБЛИЧНАЯ ФУНКЦИЯ
# ---------------------------------------------------------------------------
def ask(message, history=None, context="", max_tokens=700):
    """
    Отправляет вопрос в ИИ.

    message  — текст пользователя
    history  — список [{"role": "user"|"assistant", "content": "..."}] (необязательно)
    context  — текст с данными системы (инциденты, продажи), добавляется в системный промпт

    Возвращает (текст_ответа, имя_провайдера) или (None, None), если ИИ
    не настроен или произошла ошибка — тогда вызывающий код использует правила.
    """
    provider = get_provider()
    if provider == "none":
        return None, None

    system = SYSTEM_PROMPT
    if context:
        system += "\n\nДанные системы:\n" + context

    try:
        messages = _clean_history(history, message)
        text = _CALLERS[provider](system, messages, max_tokens)
        if text:
            return text, provider
    except Exception as exc:  # сеть, ключ, лимиты —/demo не должно падать
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = " | ответ сервера: " + resp.text[:300].replace("\n", " ")
            except Exception:
                pass
        print(f"[AI ERROR] provider={provider}: {exc}{detail}")
    return None, None
