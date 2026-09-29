"""Client LLM compatibile OpenAI con output strutturato robusto.

Strategia (dal più economico al più costoso):
1. OpenRouter: parametro nativo `models` = fallback automatico se il primo modello è saturo/non disponibile.
2. Validazione Pydantic dell'output. Se fallisce: nuovo tentativo reinserendo l'errore di validazione
   nella conversazione, così il modello corregge il proprio JSON.
3. Se anche il secondo tentativo fallisce: si passa al gruppo di modelli successivo.
"""
import json
import re
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from . import config

T = TypeVar("T", bound=BaseModel)

# Ordine di preferenza per la scoperta automatica: stessa famiglia del modello self-hosted prima.
PREFERRED_FREE = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]
_EXCLUDE = ("safety", "code", "lyria", "omni", "-fin", "-sante")
_models_cache: tuple[float, list[str]] | None = None


class LLMError(RuntimeError):
    pass


def free_models() -> list[str]:
    """Modelli free che dichiarano supporto a JSON/structured output, in ordine di preferenza."""
    global _models_cache
    if _models_cache and time.time() - _models_cache[0] < 3600:
        return _models_cache[1]
    try:
        data = httpx.get("https://openrouter.ai/api/v1/models", timeout=15).json()["data"]
    except Exception:
        return PREFERRED_FREE
    ok = set()
    for m in data:
        mid, sp = m["id"], m.get("supported_parameters", [])
        if not (mid.endswith(":free") or mid == "openrouter/free"):
            continue
        if any(x in mid for x in _EXCLUDE):
            continue
        if "structured_outputs" in sp or "response_format" in sp:
            ok.add(mid)
    ranked = [m for m in PREFERRED_FREE if m in ok] + sorted(ok - set(PREFERRED_FREE))
    _models_cache = (time.time(), ranked or PREFERRED_FREE)
    return _models_cache[1]


def model_groups() -> list[list[str]]:
    models = free_models() if config.LLM_MODELS == ["auto:free"] else config.LLM_MODELS
    if not config.IS_OPENROUTER:
        return [[m] for m in models]
    # OpenRouter accetta fino a 3 modelli nel fallback nativo
    return [models[i:i + 3] for i in range(0, len(models), 3)]


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        return fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _chat(models: list[str], messages: list[dict], temperature: float, timeout: float) -> tuple[str, str]:
    body: dict = {"messages": messages, "temperature": temperature, "response_format": {"type": "json_object"}}
    if config.IS_OPENROUTER:
        body["models"] = models
    else:
        body["model"] = models[0]
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}
    if config.IS_OPENROUTER:
        headers["X-Title"] = "AI Brief demo"
    r = httpx.post(f"{config.LLM_BASE_URL}/chat/completions", json=body, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    if "choices" not in data:
        raise LLMError(f"risposta senza choices: {str(data)[:300]}")
    return data["choices"][0]["message"]["content"] or "", data.get("model", models[0])


def structured(system: str, user: str, schema: type[T], temperature: float = 0.2,
               call_timeout: float | None = None, deadline: float | None = None) -> tuple[T, dict]:
    """Chiama l'LLM e restituisce un oggetto validato + metadati (modello usato, tentativi).
    deadline = istante (time.time) entro cui chiudere: su Vercel ogni passaggio ha un tetto di durata, quindi
    tentativi e fallback si fermano prima di sforarlo invece di far scadere la funzione."""
    if not config.LLM_API_KEY:
        raise LLMError("LLM_API_KEY non configurata")
    schema_hint = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    system_full = f"{system}\n\nRispondi SOLO con un oggetto JSON valido conforme a questo JSON Schema:\n{schema_hint}"
    attempts: list[str] = []
    deadline = deadline or time.time() + config.STEP_BUDGET  # ogni passaggio resta nel tetto della funzione
    for group in model_groups():
        messages = [{"role": "system", "content": system_full}, {"role": "user", "content": user}]
        for attempt in range(2):
            remaining = (deadline - time.time()) if deadline else None
            if remaining is not None and remaining < 20:
                raise LLMError("tempo esaurito per questo passaggio: " + " | ".join(attempts[-4:]))
            timeout = call_timeout or config.LLM_TIMEOUT
            if remaining is not None:
                timeout = min(timeout, remaining - 5)
            try:
                raw, used = _chat(group, messages, temperature, timeout)
            except httpx.TimeoutException:
                attempts.append(f"{group[0]}: timeout (tentativo {attempt + 1})")
                continue  # modello lento/carico: riprovo una volta sullo stesso gruppo
            except (LLMError, httpx.HTTPError) as e:
                attempts.append(f"{group[0]}: {e}")
                break  # errore di trasporto/quota: gruppo successivo
            try:
                obj = schema.model_validate_json(_extract_json(raw))
                return obj, {"model": used, "attempts": attempts + [f"{used}: ok (tentativo {attempt + 1})"]}
            except ValidationError as e:
                attempts.append(f"{used}: JSON non valido (tentativo {attempt + 1})")
                # reinserisco l'errore: il modello vede cosa ha sbagliato e corregge
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Il JSON non è valido rispetto allo schema:\n{str(e)[:1500]}\nRestituisci il JSON corretto e completo, senza testo aggiuntivo."},
                ]
    raise LLMError("tutti i modelli hanno fallito: " + " | ".join(attempts[-6:]))
