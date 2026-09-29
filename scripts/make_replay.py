"""Esegue l'intera pipeline una volta e salva il run congelato in public/replay.json (modalità replay = golden run).

Uso: python scripts/make_replay.py   (usa LLM_BASE_URL / LLM_API_KEY / LLM_MODELS dal .env)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, pipeline, recipients, sources  # noqa: E402

# Il replay si genera con un gateway dedicato (es. modello self-hosted, senza limiti di richieste):
# REPLAY_LLM_BASE_URL / REPLAY_LLM_API_KEY / REPLAY_LLM_MODELS. Se assenti, usa la configurazione live.
if os.getenv("REPLAY_LLM_BASE_URL"):
    config.LLM_BASE_URL = os.environ["REPLAY_LLM_BASE_URL"].rstrip("/")
    config.LLM_API_KEY = os.getenv("REPLAY_LLM_API_KEY", "")
    config.LLM_MODELS = [m.strip() for m in os.getenv("REPLAY_LLM_MODELS", "").split(",") if m.strip()]
    config.IS_OPENROUTER = "openrouter.ai" in config.LLM_BASE_URL
    config.LLM_TIMEOUT = float(os.getenv("REPLAY_LLM_TIMEOUT", "180"))
    config.WRITE_TIMEOUT = max(config.WRITE_TIMEOUT, config.LLM_TIMEOUT)
    config.STEP_BUDGET = 1200  # in locale non c'è il tetto dei 300 s  # fuori da Vercel non c'è il limite dei 60 s


def step(name, fn, *args):
    t = time.time()
    print(f"→ {name}…", flush=True)
    res = fn(*args)
    meta = res.get("meta", {}) if isinstance(res, dict) else {}
    print(f"  ok in {time.time() - t:.1f}s {meta.get('model', '')}", flush=True)
    return res


def main():
    run = {"id": "replay", "creato": datetime.now(timezone.utc).isoformat(), "log": []}
    res = step("pianificazione query", pipeline.plan)
    run["queries"] = res["queries"]; run["log"].append({"passo": "pianificazione", **res["meta"]})

    run["articles"] = step("recupero", lambda: {"a": sources.fetch(run["queries"], {})})["a"]
    run["log"].append({"passo": "recupero", "model": "— (codice)", "attempts": [f"{len(run['articles'])} articoli dopo pulizia e dedup"]})

    run["evals"] = []
    for i in range(0, len(run["articles"]), config.EXTRACT_BATCH):
        res = step(f"estrazione lotto {i // config.EXTRACT_BATCH + 1}", pipeline.extract, run["articles"][i:i + config.EXTRACT_BATCH])
        run["evals"] += res["evals"]; run["log"].append({"passo": f"estrazione lotto {i // config.EXTRACT_BATCH + 1}", **res["meta"]})

    res = step("raggruppamento", pipeline.cluster, run["articles"], run["evals"])
    run["themes"] = res["themes"]; run["selected"] = pipeline.select(res["themes"])
    run["log"].append({"passo": "raggruppamento e selezione", **res["meta"]})
    run["approved"] = pipeline.pool_rule(run["evals"], run["themes"], run["selected"])
    run["editor_note"] = "Replay: approvazione del redattore simulata con la regola rilevanza ≥ 2 e hype ≤ 3."

    themes = [t for t in run["themes"] if t["id"] in run["selected"]]
    res = step("redazione", pipeline.write, themes, run["articles"], run["evals"])
    run["core"] = res["core"]; run["qa"] = pipeline.qa(res["core"], themes)
    run["log"].append({"passo": "redazione", **res["meta"]})

    run["variants"] = {}
    for key, seg in recipients.segments().items():
        res = step(f"variante {seg['settore']}", pipeline.personalize, run["core"], seg)
        run["variants"][key] = res["variant"]; run["log"].append({"passo": f"variante {seg['settore']}", **res["meta"]})

    label = os.getenv("REPLAY_LLM_LABEL")  # es. l'alias servito da vLLM non coincide con il nome dei pesi
    if label:
        for l in run["log"]:
            l["model"] = l["model"].replace(config.LLM_MODELS[0], label)
            l["attempts"] = [a.replace(config.LLM_MODELS[0], label) for a in l.get("attempts", [])]
    out = Path(__import__("os").getenv("REPLAY_PATH", ROOT / "public" / "replay.json"))
    out.write_text(json.dumps(run, ensure_ascii=False, indent=1))
    print(f"\nsalvato {out} — QA:")
    for c in run["qa"]:
        print(f"  {'✓' if c['ok'] else '✗'} {c['check']}: {c['dettaglio']}")


if __name__ == "__main__":
    main()
