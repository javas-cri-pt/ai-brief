"""API della demo (FastAPI). Su Vercel gira come un'unica funzione Python, in locale con uvicorn.

Modalità replay: legge public/replay.json (run congelato), non consuma LLM.
Modalità live: ogni passaggio della pipeline è un endpoint protetto da codice di accesso e da tetti giornalieri.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core import config, llm, pipeline, recipients, sources  # noqa: E402
from core.store import BACKEND, kv  # noqa: E402

app = FastAPI(title="AI Brief demo")
REPLAY = Path(__import__("os").getenv("REPLAY_PATH", ROOT / "public" / "replay.json"))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_run(run_id: str) -> dict:
    if run_id == "replay":
        if not REPLAY.exists():
            raise HTTPException(404, "replay non ancora generato")
        return json.loads(REPLAY.read_text())
    run = kv.get(f"run:{run_id}")
    if not run:
        raise HTTPException(404, "run non trovato")
    return run


def _save_run(run: dict) -> None:
    if run["id"] != "replay":
        kv.set(f"run:{run['id']}", run)


def _consume(code: str | None) -> None:
    """Codice di accesso verificato lato server + tetto per codice + tetto globale (protegge la quota del gateway)."""
    if not code or code not in config.ACCESS_CODES:
        raise HTTPException(401, "codice di accesso non valido")
    if kv.incr(f"usage:{code}:{_today()}", 86400) > config.ACCESS_CODES[code]:
        raise HTTPException(429, "tetto giornaliero raggiunto per questo codice")
    if kv.incr(f"usage:all:{_today()}", 86400) > config.GLOBAL_DAILY_CAP:
        raise HTTPException(429, "tetto giornaliero globale raggiunto")


def _owned_run(run_id: str, code: str | None) -> dict:
    """Ogni run live appartiene al codice che l'ha creato: un altro codice valido non può modificarlo."""
    run = _load_run(run_id)
    if run_id == "replay" or code != run.get("codice"):
        raise HTTPException(403, "run non modificabile con questo codice")
    return run


def _llm_call(fn, *args):
    try:
        return fn(*args)
    except llm.LLMError as e:
        raise HTTPException(502, f"LLM non disponibile, usa il replay. Dettaglio: {e}")


# ------------------------------------------------------------------ info
@app.get("/api/config")
def get_config():
    return {"brand": config.BRAND_NAME, "editore": config.PUBLISHER_NAME, "sezione_editore": config.PUBLISHER_SECTION,
            "live_disponibile": bool(config.LLM_API_KEY and config.ACCESS_CODES), "storage": BACKEND,
            "gateway": "OpenRouter" if config.IS_OPENROUTER else "self-hosted", "replay": REPLAY.exists(),
            "destinatari": [{"token": r["token"], "ruolo": r["ruolo"], "organizzazione": r["organizzazione"]} for r in recipients.RECIPIENTS]}


class Code(BaseModel):
    code: str


@app.post("/api/check-code")
def check_code(body: Code):
    if body.code not in config.ACCESS_CODES:
        raise HTTPException(401, "codice non valido")
    used = kv.get(f"usage:{body.code}:{_today()}") or 0
    used_all = kv.get(f"usage:all:{_today()}") or 0
    residuo = max(0, min(config.ACCESS_CODES[body.code] - used, config.GLOBAL_DAILY_CAP - used_all))
    # una run live usa circa CALLS_PER_RUN chiamate: al lettore mostriamo run, non chiamate
    return {"ok": True, "residuo": residuo, "run_rimaste": residuo // config.CALLS_PER_RUN}


# ------------------------------------------------------------------ pipeline live, un passaggio per chiamata
@app.post("/api/live/start")
def live_start(x_access_code: str | None = Header(None)):
    _consume(x_access_code)
    run = {"id": uuid.uuid4().hex[:8], "creato": datetime.now(timezone.utc).isoformat(), "codice": x_access_code,
           "log": [], "articles": [], "evals": [], "approved": []}
    res = _llm_call(pipeline.plan)
    run["queries"] = res["queries"]; run["log"].append({"passo": "pianificazione", **res["meta"]})
    _save_run(run)
    kv.push("runs", {"id": run["id"], "creato": run["creato"]})
    return run


@app.post("/api/live/{run_id}/fetch")
def live_fetch(run_id: str, x_access_code: str | None = Header(None)):
    run = _owned_run(run_id, x_access_code)
    trust = {d: v["valore"] for d, v in pipeline.source_trust().items()}
    run["articles"] = sources.fetch(run["queries"], trust)
    run["log"].append({"passo": "recupero", "model": "— (codice)", "attempts": [f"{len(run['articles'])} articoli dopo pulizia e dedup"]})
    _save_run(run)
    return {"articles": run["articles"]}


@app.post("/api/live/{run_id}/extract/{batch}")
def live_extract(run_id: str, batch: int, x_access_code: str | None = Header(None)):
    run = _owned_run(run_id, x_access_code)
    _consume(x_access_code)
    chunk = run["articles"][batch * config.EXTRACT_BATCH:(batch + 1) * config.EXTRACT_BATCH]
    if not chunk:
        return {"evals": [], "done": True}
    res = _llm_call(pipeline.extract, chunk)
    run["evals"] = [e for e in run["evals"] if e["id"] not in {x["id"] for x in res["evals"]}] + res["evals"]
    run["log"].append({"passo": f"estrazione lotto {batch + 1}", **res["meta"]})
    _save_run(run)
    return {"evals": res["evals"], "done": (batch + 1) * config.EXTRACT_BATCH >= len(run["articles"])}


@app.post("/api/live/{run_id}/cluster")
def live_cluster(run_id: str, x_access_code: str | None = Header(None)):
    run = _owned_run(run_id, x_access_code)
    _consume(x_access_code)
    res = _llm_call(pipeline.cluster, run["articles"], run["evals"])
    run["themes"] = res["themes"]
    run["selected"] = pipeline.select(res["themes"])
    # proposta iniziale al redattore: gli articoli dei temi selezionati + quelli rilevanti e poco hype
    run["approved"] = pipeline.pool_rule(run["evals"], run["themes"], run["selected"])
    run["log"].append({"passo": "raggruppamento e selezione", **res["meta"]})
    _save_run(run)
    return {"themes": run["themes"], "selected": run["selected"], "approved": run["approved"]}


class Approval(BaseModel):
    selected: list[str]
    approved: list[str]


@app.post("/api/run/{run_id}/editor")
def editor_approve(run_id: str, body: Approval, x_access_code: str | None = Header(None)):
    """Review layer: il redattore approva il pool (e i temi del nucleo comune), non le singole newsletter."""
    run = _owned_run(run_id, x_access_code)
    run["selected"], run["approved"] = body.selected, body.approved
    _save_run(run)
    return {"ok": True}


@app.post("/api/live/{run_id}/write")
def live_write(run_id: str, x_access_code: str | None = Header(None)):
    run = _owned_run(run_id, x_access_code)
    _consume(x_access_code)
    themes = [t for t in run["themes"] if t["id"] in run["selected"]]
    res = _llm_call(pipeline.write, themes, run["articles"], run["evals"])
    run["core"] = res["core"]
    run["qa"] = pipeline.qa(res["core"], themes)
    run["log"].append({"passo": "redazione", **res["meta"]})
    run["variants"] = {}
    _save_run(run)
    return {"core": run["core"], "qa": run["qa"], "segmenti": list(recipients.segments())}


@app.post("/api/live/{run_id}/personalize/{n}")
def live_personalize(run_id: str, n: int, x_access_code: str | None = Header(None)):
    run = _owned_run(run_id, x_access_code)
    _consume(x_access_code)
    key, seg = list(recipients.segments().items())[n]
    res = _llm_call(pipeline.personalize, run["core"], seg)
    run["variants"][key] = res["variant"]
    run["log"].append({"passo": f"variante {seg['settore']}", **res["meta"]})
    _save_run(run)
    return {"segmento": key, "variant": res["variant"], "done": n + 1 >= len(recipients.segments())}


# ------------------------------------------------------------------ lettura, invio simulato, registro
@app.get("/api/run/{run_id}")
def get_run(run_id: str):
    run = _load_run(run_id)
    run.pop("codice", None)
    return run


@app.get("/api/run/{run_id}/render/{token}")
def render(run_id: str, token: str):
    if token not in recipients.BY_TOKEN:
        raise HTTPException(404, "destinatario sconosciuto")
    run = _load_run(run_id)
    return pipeline.render_for(token, run)


@app.post("/api/run/{run_id}/send")
def send(run_id: str, x_access_code: str | None = Header(None)):
    """Invio simulato: nessuna email parte, ma si registra chi ha ricevuto cosa (consultabile dal team)."""
    run = _load_run(run_id) if run_id == "replay" else _owned_run(run_id, x_access_code)
    ts = datetime.now(timezone.utc).isoformat()
    for r in recipients.RECIPIENTS:
        nl = pipeline.render_for(r["token"], run)
        kv.push("deliveries", {"run": run_id, "token": r["token"], "ts": ts, "oggetto": nl["oggetto"],
                               "notizie": [n["titolo"] for n in nl["notizie"]],
                               "personalizzate": [p["titolo"] for p in nl["personalizzate"]]})
    return {"ok": True, "destinatari": len(recipients.RECIPIENTS)}


@app.get("/api/deliveries")
def deliveries():
    return list(reversed(kv.items("deliveries")))[:60]


@app.get("/api/trust")
def trust(run_id: str = "replay"):
    try:
        domains = [a["dominio"] for a in _load_run(run_id)["articles"]]
    except HTTPException:
        domains = []
    return pipeline.source_trust(domains)


# ------------------------------------------------------------------ pannello personale (link con token, senza login)
@app.get("/api/panel/{token}")
def panel(token: str):
    r = recipients.BY_TOKEN.get(token)
    if not r:
        raise HTTPException(404, "link non valido")
    prefs = kv.get(f"prefs:{token}") or {}
    return {"profilo": {k: v for k, v in r.items() if k != "token"}, "interessi": prefs.get("interessi", r["interessi"]),
            "feedback": list(reversed(pipeline.feedback_events(token))),
            "ricevute": [d for d in reversed(kv.items("deliveries")) if d["token"] == token][:20]}


class Feedback(BaseModel):
    token: str
    tipo: str  # notizia | fonte
    valore: str  # interessa | non_interessa | affidabile | non_affidabile
    run: str = "replay"
    item: str = ""
    titolo: str = ""
    dominio: str = ""
    tipo_notizia: str = ""


@app.post("/api/feedback")
def feedback(fb: Feedback):
    """Arriva solo da POST dopo la pagina di conferma: gli scanner di sicurezza (Safe Links ecc.) fanno GET e non votano."""
    ok = {"notizia": {"interessa", "non_interessa"}, "fonte": {"affidabile", "non_affidabile"}}
    if fb.token not in recipients.BY_TOKEN or fb.valore not in ok.get(fb.tipo, set()):
        raise HTTPException(400, "feedback non valido")
    kv.push(f"fb:{fb.token}", {**fb.model_dump(exclude={"token"}), "ts": datetime.now(timezone.utc).isoformat()})
    return {"ok": True}


class Prefs(BaseModel):
    token: str
    interessi: list[str]


@app.post("/api/prefs")
def prefs(p: Prefs):
    if p.token not in recipients.BY_TOKEN:
        raise HTTPException(404, "link non valido")
    clean = [i.strip().lower()[:40] for i in p.interessi if i.strip()][:10]
    kv.set(f"prefs:{p.token}", {"interessi": clean})
    return {"ok": True, "interessi": clean}


# ------------------------------------------------------------------ statico (solo locale: su Vercel lo serve la CDN)
if not __import__("os").getenv("VERCEL"):
    @app.get("/pannello/{token}")
    def pannello_page(token: str):
        return FileResponse(ROOT / "public" / "pannello.html")

    @app.get("/")
    def home():
        return FileResponse(ROOT / "public" / "index.html")

    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="static")
