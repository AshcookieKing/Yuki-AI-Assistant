"""Единый клиент к языковым моделям.
Внутренний формат сообщений — как у OpenAI (role/content/tool_calls/tool_call_id).
Провайдеры: локальная Ollama и любые OpenAI-совместимые облака (Groq, Gemini, OpenRouter)."""
import json
import uuid

import httpx

from . import settings

CLOUD_PRESETS = {
    "groq": {"title": "Groq — очень быстро, бесплатно", "url": "https://api.groq.com/openai/v1",
             "models": ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "openai/gpt-oss-20b"],
             "key_url": "https://console.groq.com/keys"},
    "gemini": {"title": "Google Gemini — очень умная, бесплатно (лимит в день)",
               "url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "models": ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash"],
               "key_url": "https://aistudio.google.com/apikey"},
    "openrouter": {"title": "OpenRouter — много бесплатных моделей (:free)", "url": "https://openrouter.ai/api/v1",
                   "models": ["deepseek/deepseek-chat-v3-0324:free", "qwen/qwen3-coder:free"],
                   "key_url": "https://openrouter.ai/keys"},
}


class LLMError(Exception):
    pass


def _role_cfg(role: str) -> dict:
    """role: 'agent' (действия) или 'chat' (характер). У каждого может быть своя модель."""
    s = settings.get()
    provider = s.get(f"{role}_provider") or s.get("llm_provider", "ollama")
    if provider == "same":
        provider = s.get("llm_provider", "ollama")
    if provider == "ollama":
        model = s.get(f"{role}_model") or s["llm_model"]
        return {"provider": "ollama", "url": s["ollama_url"], "model": model, "key": ""}
    preset = CLOUD_PRESETS.get(provider, {})
    return {
        "provider": provider,
        "url": s.get(f"{provider}_url") or preset.get("url", ""),
        "model": s.get(f"{provider}_model") or (preset.get("models") or [""])[0],
        "key": s.get(f"{provider}_key", ""),
    }


def describe(role: str) -> str:
    c = _role_cfg(role)
    return f"{c['provider']}:{c['model']}"


# ---------- Ollama ----------
def _to_ollama(messages):
    out = []
    for m in messages:
        m2 = {"role": m["role"], "content": m.get("content") or ""}
        if m.get("images"):
            m2["images"] = m["images"]
        if m.get("tool_calls"):
            m2["tool_calls"] = [{"function": {"name": tc["function"]["name"],
                                              "arguments": json.loads(tc["function"]["arguments"] or "{}")}}
                                for tc in m["tool_calls"]]
        if m["role"] == "tool":
            m2["tool_name"] = m.get("name", "")
        out.append(m2)
    return out


def _from_ollama_calls(calls):
    return [{"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
             "function": {"name": c["function"]["name"],
                          "arguments": json.dumps(c["function"].get("arguments") or {}, ensure_ascii=False)}}
            for c in calls or []]


# ---------- OpenAI-совместимые ----------
def _to_openai(messages):
    out = []
    for m in messages:
        if m.get("images"):
            parts = [{"type": "text", "text": m.get("content") or ""}]
            parts += [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}} for b in m["images"]]
            out.append({"role": m["role"], "content": parts})
            continue
        m2 = {k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "tool_call_id", "name")}
        if m2["role"] == "tool":
            m2.pop("name", None)
        if m2.get("content") is None:
            m2["content"] = ""
        out.append(m2)
    return out


_no_think: set[str] = set()


async def complete(role, messages, tools=None, think=False, temperature=0.7, max_tokens=None):
    """Ответ целиком: {'content': str, 'tool_calls': [...]}"""
    c = _role_cfg(role)
    s = settings.get()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as cl:
        if c["provider"] == "ollama":
            body = {"model": c["model"], "messages": _to_ollama(messages), "stream": False, "keep_alive": "30m",
                    "options": {"temperature": temperature, "num_ctx": int(s["num_ctx"])}}
            if max_tokens:
                body["options"]["num_predict"] = max_tokens
            if tools:
                body["tools"] = tools
            if c["model"] not in _no_think:
                body["think"] = think
            r = await cl.post(f"{c['url']}/api/chat", json=body)
            if r.status_code != 200 and "think" in r.text.lower() and "think" in body:
                _no_think.add(c["model"])
                body.pop("think")
                r = await cl.post(f"{c['url']}/api/chat", json=body)
            if r.status_code != 200:
                raise LLMError(_err(r.text))
            msg = r.json().get("message", {})
            return {"content": msg.get("content", ""), "tool_calls": _from_ollama_calls(msg.get("tool_calls"))}

        if not c["key"]:
            raise LLMError(f"Не задан API-ключ для {c['provider']} (Настройки → Модель).")
        body = {"model": c["model"], "messages": _to_openai(messages), "temperature": temperature}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
        if think and c["provider"] in ("groq", "gemini"):
            body["reasoning_effort"] = "medium"
        r = await cl.post(f"{c['url']}/chat/completions", json=body, headers=_auth(c))
        if r.status_code != 200 and "reasoning" in r.text.lower():
            body.pop("reasoning_effort", None)
            r = await cl.post(f"{c['url']}/chat/completions", json=body, headers=_auth(c))
        if r.status_code != 200:
            raise LLMError(_err(r.text))
        msg = r.json()["choices"][0]["message"]
        return {"content": msg.get("content") or "", "tool_calls": msg.get("tool_calls") or []}


async def stream(role, messages, think=False, temperature=0.8, max_tokens=None):
    """Поток кусочков текста ответа."""
    c = _role_cfg(role)
    s = settings.get()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as cl:
        if c["provider"] == "ollama":
            body = {"model": c["model"], "messages": _to_ollama(messages), "stream": True, "keep_alive": "30m",
                    "options": {"temperature": temperature, "num_ctx": int(s["num_ctx"])}}
            if max_tokens:
                body["options"]["num_predict"] = max_tokens
            if c["model"] not in _no_think:
                body["think"] = think
            async with cl.stream("POST", f"{c['url']}/api/chat", json=body) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode("utf-8", "replace")
                    if "think" in text.lower():
                        _no_think.add(c["model"])
                    raise LLMError(_err(text))
                async for line in r.aiter_lines():
                    if line.strip():
                        delta = json.loads(line).get("message", {}).get("content", "")
                        if delta:
                            yield delta
            return

        if not c["key"]:
            raise LLMError(f"Не задан API-ключ для {c['provider']} (Настройки → Модель).")
        body = {"model": c["model"], "messages": _to_openai(messages), "temperature": temperature, "stream": True}
        if max_tokens:
            body["max_tokens"] = max_tokens
        async with cl.stream("POST", f"{c['url']}/chat/completions", json=body, headers=_auth(c)) as r:
            if r.status_code != 200:
                raise LLMError(_err((await r.aread()).decode("utf-8", "replace")))
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content")
                except Exception:
                    delta = None
                if delta:
                    yield delta


def _auth(c):
    h = {"Authorization": f"Bearer {c['key']}"}
    if c["provider"] == "openrouter":
        h["X-Title"] = "Yuki Assistant"
    return h


def _err(text: str) -> str:
    try:
        j = json.loads(text)
        e = j.get("error", j)
        return e.get("message", str(e)) if isinstance(e, dict) else str(e)
    except Exception:
        return text[:300]


async def list_cloud_models(provider: str) -> list[str]:
    s = settings.get()
    preset = CLOUD_PRESETS[provider]
    key = s.get(f"{provider}_key", "")
    if not key:
        return preset["models"]
    try:
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get(f"{preset['url']}/models", headers={"Authorization": f"Bearer {key}"})
            ids = [m["id"].removeprefix("models/") for m in r.json().get("data", [])]
            if provider == "openrouter":
                ids = [i for i in ids if i.endswith(":free")]
            return sorted(ids) or preset["models"]
    except Exception:
        return preset["models"]
