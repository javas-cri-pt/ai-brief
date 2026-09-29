"""Passaggi della pipeline. Ognuno è una funzione pura (input -> output) esposta come endpoint separato:
così ogni chiamata resta sotto il limite di durata delle serverless function e il frontend mostra
l'avanzamento passo per passo.

LLM: pianificazione query, estrazione, raggruppamento, redazione, varianti di segmento.
Codice: recupero, pulizia, punteggio, vincolo di diversità, collegamento delle fonti, QA, sezione personalizzata.
"""
import json
import re
from collections import Counter
import time
from datetime import datetime, timezone

from . import config, llm, prompts, recipients
from .models import ClusterResult, ExtractBatch, NewsletterCore, QueryPlan, SegmentVariant
from .sources import DEFAULT_TRUST, prior_trust
from .store import kv

TIPO_LABEL = {"regolazione": "Regolazione", "caso_uso": "Caso d'uso", "investimento": "Investimenti", "prodotto": "Prodotto",
              "ricerca": "Ricerca", "competenze": "Competenze", "policy_pubblica": "Policy pubblica"}


# ------------------------------------------------------------------ 1. pianificazione (agente)
def plan() -> dict:
    oggi = datetime.now(timezone.utc).strftime("%d/%m/%Y")
    obj, meta = llm.structured(prompts.PLANNER, f"Oggi è il {oggi}. Pianifica le ricerche per l'uscita di questa quindicina "
                               f"(notizie degli ultimi 14 giorni).", QueryPlan, 0.5)
    queries = [q.model_dump() for q in obj.queries]
    # le query sugli interessi dei lettori le aggiunge il codice: allargano il POOL (sezione personalizzata),
    # non cambiano i temi del nucleo comune, che restano scelti per rilevanza generale
    for interesse in reader_interests():
        queries.append({"query": f"{interesse} intelligenza artificiale", "tema": "casi_uso", "lingua": "it", "origine": "interessi lettori"})
    return {"queries": queries, "meta": meta}


def reader_interests(limit: int = 8) -> list[str]:
    """Interessi aggregati della base lettori (preferenze salvate dal pannello, altrimenti quelle del profilo)."""
    c = Counter()
    for r in recipients.RECIPIENTS:
        c.update((kv.get(f"prefs:{r['token']}") or {}).get("interessi", r["interessi"]))
    return [i for i, _ in c.most_common(limit)]


# ------------------------------------------------------------------ 4. estrazione (a lotti)
def extract(articles: list[dict]) -> dict:
    payload = [{"id": a["id"], "titolo": a["titolo"], "testata": a["testata"], "estratto": a["estratto"][:350]} for a in articles]
    obj, meta = llm.structured(prompts.EXTRACTOR, json.dumps(payload, ensure_ascii=False), ExtractBatch)
    known = {a["id"] for a in articles}
    return {"evals": [e.model_dump() for e in obj.items if e.id in known], "meta": meta}


# ------------------------------------------------------------------ 5-6. raggruppamento + punteggio + diversità
def cluster(articles: list[dict], evals: list[dict]) -> dict:
    # scarto a monte ciò che non merita il costo del raggruppamento
    ev = {e["id"]: e for e in evals if e["rilevanza"] >= 3 and e["hype"] <= 3}
    payload = [{"id": i, "sintesi": e["sintesi"], "tema": e["tema"]} for i, e in ev.items()]
    obj, meta = llm.structured(prompts.CLUSTERER, json.dumps(payload, ensure_ascii=False), ClusterResult)
    art = {a["id"]: a for a in articles}
    used, themes = set(), []
    for n, c in enumerate(obj.temi):
        ids = [i for i in c.articoli if i in ev and i not in used]  # l'LLM può sbagliare id: filtro in codice
        if not ids:
            continue
        used.update(ids)
        themes.append(score_theme(f"t{n + 1}", c.titolo, [art[i] for i in ids], [ev[i] for i in ids]))
    return {"themes": sorted(themes, key=lambda t: -t["score"]), "meta": meta}


def score_theme(tid: str, titolo: str, arts: list[dict], evs: list[dict]) -> dict:
    fonti_indip = len({a["dominio"] for a in arts})
    rel = sum(e["rilevanza"] for e in evs) / len(evs)
    hype = sum(e["hype"] for e in evs) / len(evs)
    trust = sum(a["affidabilita"] for a in arts) / len(arts)
    # punteggio: rilevanza e affidabilità pesano di più; le fonti indipendenti premiano i temi confermati; l'hype penalizza
    score = 0.45 * (rel - 1) / 4 + 0.30 * trust + 0.25 * min(1, fonti_indip / 3) - 0.15 * (hype - 1) / 4
    tipo = Counter(e["tipo"] for e in evs).most_common(1)[0][0]
    return {"id": tid, "titolo": titolo, "articoli": [a["id"] for a in arts], "tipo": tipo,
            "fonti_indipendenti": fonti_indip, "rilevanza": round(rel, 2), "hype": round(hype, 2),
            "affidabilita": round(trust, 2), "score": round(score, 3)}


def select(themes: list[dict], k_min: int = 5, k_max: int = 7) -> list[str]:
    """Vincolo di diversità: max 2 temi per tipo, almeno 1 su regolazione/policy se disponibile."""
    ranked = sorted(themes, key=lambda t: -t["score"])
    chosen, per_tipo = [], Counter()
    gov = next((t for t in ranked if t["tipo"] in ("regolazione", "policy_pubblica")), None)
    if gov:
        chosen.append(gov); per_tipo[gov["tipo"]] += 1
    for t in ranked:
        if len(chosen) >= k_max:
            break
        if t not in chosen and per_tipo[t["tipo"]] < 2:
            chosen.append(t); per_tipo[t["tipo"]] += 1
    for t in ranked:  # se la diversità lascia meno di k_min temi, completo con i migliori rimasti
        if len(chosen) >= k_min:
            break
        if t not in chosen:
            chosen.append(t)
    return [t["id"] for t in sorted(chosen, key=lambda t: -t["score"])]


# ------------------------------------------------------------------ 7. redazione (A.2)
def write(themes: list[dict], articles: list[dict], evals: list[dict]) -> dict:
    art = {a["id"]: a for a in articles}
    ev = {e["id"]: e for e in evals}
    payload = [{"tema_id": t["id"], "titolo_tema": t["titolo"],
                "articoli": [{"id": i, "testata": art[i]["testata"], "data": art[i]["data"][:10],
                              "titolo": art[i]["titolo"], "estratto": art[i]["estratto"][:400],
                              "sintesi": ev.get(i, {}).get("sintesi", "")} for i in t["articoli"]]}
               for t in themes]
    # la redazione è la chiamata più lunga (circa 2.000 parole): timeout ampio e scadenza complessiva del passaggio
    deadline = time.time() + config.STEP_BUDGET
    obj, meta = llm.structured(prompts.EDITOR, "Temi approvati per questa uscita:\n" + json.dumps(payload, ensure_ascii=False),
                               NewsletterCore, 0.4, call_timeout=config.WRITE_TIMEOUT, deadline=deadline)
    core = obj.model_dump()
    n_words = _words(core)
    if n_words < 1500 and deadline - time.time() > config.WRITE_TIMEOUT * 0.6:
        # QA → correzione: il redattore rivede la propria bozza con l'errore misurato. Solo i commenti si allungano,
        # i fatti restano quelli (più testo non deve significare più informazioni inventate).
        fix = (f"Temi approvati:\n{json.dumps(payload, ensure_ascii=False)}\n\nBOZZA ATTUALE ({n_words} parole, sotto il minimo di 1.500):\n"
               f"{json.dumps(core, ensure_ascii=False)}\n\nRestituisci la newsletter completa allungando SOLO editoriale (150-200 parole) "
               "e commenti (180-250 parole ciascuno): implicazioni, rischi, cosa fare nelle prossime settimane. "
               "Non modificare titoli, fatti e fonti_ids, non aggiungere informazioni non presenti negli articoli.")
        try:
            obj2, meta2 = llm.structured(prompts.EDITOR, fix, NewsletterCore, 0.4, call_timeout=config.WRITE_TIMEOUT, deadline=deadline)
            if _words(obj2.model_dump()) > n_words:
                core = obj2.model_dump()
                meta["attempts"] = meta["attempts"] + [f"QA lunghezza: {n_words} parole → correzione → {_words(core)} parole"]
        except llm.LLMError:
            pass
    # le fonti le collega il codice: il modello indica solo gli id, che vengono verificati
    by_theme = {t["id"]: t for t in themes}
    for n in core["notizie"]:
        allowed = set(by_theme.get(n["tema_id"], {}).get("articoli", []))
        n["fonti"] = [{"id": i, "testata": art[i]["testata"], "url": art[i]["url"], "dominio": art[i]["dominio"],
                       "data": art[i]["data"][:10]} for i in n["fonti_ids"] if i in allowed]
        n["tipo"] = by_theme.get(n["tema_id"], {}).get("tipo", "")
    return {"core": core, "meta": meta}


def _words(core: dict) -> int:
    w = lambda t: len(re.findall(r"\w+", t))
    return w(core["editoriale"]) + sum(w(n["titolo"]) + w(n["fatto"]) + w(n["commento"]) for n in core["notizie"]) \
        + sum(w(p) for p in core["da_portare_in_riunione"])


# ------------------------------------------------------------------ A.3 varianti per segmento
def personalize(core: dict, segment: dict) -> dict:
    notizie = [{"tema_id": n["tema_id"], "titolo": n["titolo"], "fatto": n["fatto"], "commento": n["commento"]} for n in core["notizie"]]
    user = "SEGMENTO:\n" + json.dumps(segment, ensure_ascii=False) + "\n\nNOTIZIE:\n" + json.dumps(notizie, ensure_ascii=False)
    obj, meta = llm.structured(prompts.PERSONALIZER, user, SegmentVariant, 0.4)
    v = obj.model_dump()
    ids = [n["tema_id"] for n in core["notizie"]]
    v["ordine"] = [i for i in v["ordine"] if i in ids] + [i for i in ids if i not in v["ordine"]]
    return {"variant": v, "meta": meta}


# ------------------------------------------------------------------ QA automatico (prima del redattore umano)
def qa(core: dict, themes: list[dict]) -> list[dict]:
    words = lambda s: len(re.findall(r"\w+", s))
    total = words(core["editoriale"]) + sum(words(n["titolo"]) + words(n["fatto"]) + words(n["commento"]) for n in core["notizie"]) \
        + sum(words(p) for p in core["da_portare_in_riunione"])
    no_src = [n["titolo"] for n in core["notizie"] if not n["fonti"]]
    tipi = Counter(n.get("tipo") for n in core["notizie"])
    return [
        {"check": "Lunghezza 1.500–2.500 parole", "ok": 1500 <= total <= 2500, "dettaglio": f"{total} parole"},
        {"check": "5–7 notizie", "ok": 5 <= len(core["notizie"]) <= 7, "dettaglio": f"{len(core['notizie'])} notizie"},
        {"check": "Ogni notizia ha almeno una fonte verificata", "ok": not no_src,
         "dettaglio": "tutte collegate" if not no_src else "senza fonte: " + "; ".join(no_src)},
        {"check": "Diversità (max 2 per tipo)", "ok": max(tipi.values(), default=0) <= 2, "dettaglio": dict(tipi)},
        {"check": "Almeno un tema di regolazione/policy", "ok": bool(tipi.get("regolazione") or tipi.get("policy_pubblica")), "dettaglio": ""},
    ]


# ------------------------------------------------------------------ feedback: i due circuiti
def feedback_events(token: str) -> list[dict]:
    return kv.items(f"fb:{token}")


def source_trust(domains: list[str] | None = None) -> dict[str, dict]:
    """Circuito globale: affidabilità = prior redazionale corretto dai voti aggregati.
    Un singolo voto non basta: la correzione scatta da 2 voti netti ed è limitata a ±0.3."""
    votes: dict[str, Counter] = {}
    for r in recipients.RECIPIENTS:
        for e in feedback_events(r["token"]):
            if e["tipo"] == "fonte":
                votes.setdefault(e["dominio"], Counter())[e["valore"]] += 1
    out = {}
    for d in set(domains or []) | set(votes):
        c = votes.get(d, Counter())
        net = c["affidabile"] - c["non_affidabile"]
        prior = prior_trust(d)
        adj = max(-0.3, min(0.3, 0.1 * net)) if abs(net) >= 2 else 0.0
        out[d] = {"prior": prior, "affidabile": c["affidabile"], "non_affidabile": c["non_affidabile"],
                  "valore": round(max(0.0, min(1.0, prior + adj)), 2)}
    return out


def pool_rule(evals: list[dict], themes: list[dict], selected: list[str]) -> list[str]:
    """Proposta iniziale del pool al redattore: articoli dei temi scelti + notizie con hype basso e rilevanza almeno 2.
    La soglia di rilevanza è più bassa di quella del nucleo: una notizia di nicchia (es. IA nella moda) prende voti bassi
    dal giudice, che valuta per il lettore generico, ma può essere la più utile per chi ha quell'interesse."""
    sel_arts = {a for t in themes if t["id"] in selected for a in t["articoli"]}
    return sorted(sel_arts | {e["id"] for e in evals if e["rilevanza"] >= 2 and e["hype"] <= 3})


def personal_section(token: str, run: dict, k: int = 2) -> list[dict]:
    """Circuito per utente (calcolato in codice, nessuna chiamata LLM): 1-2 notizie in più, scelte SOLO dal pool
    approvato dal redattore e non già nel nucleo comune, in base a interessi e feedback della persona."""
    r = recipients.BY_TOKEN[token]
    prefs = kv.get(f"prefs:{token}") or {}
    interessi = [i.lower() for i in prefs.get("interessi", r["interessi"])]
    events = feedback_events(token)
    fonti_no = {e["dominio"] for e in events if e["tipo"] == "fonte" and e["valore"] == "non_affidabile"}
    art = {a["id"]: a for a in run["articles"]}
    liked_tipi = Counter(); disliked = set()
    for e in events:
        if e["tipo"] == "notizia":
            (disliked.add(e["item"]) if e["valore"] == "non_interessa" else liked_tipi.update([e.get("tipo_notizia", "")]))
    in_core = {i for n in run["core"]["notizie"] for i in (f["id"] for f in n["fonti"])}
    cands = []
    for e in run["evals"]:
        a = art.get(e["id"])
        if not a or e["id"] not in run["approved"] or e["id"] in in_core or e["id"] in disliked or a["dominio"] in fonti_no:
            continue
        testo = " ".join([*e["tags"], *e["settori"], e["sintesi"], a["titolo"]]).lower()
        # frase intera, oppure tutte le parole significative (evita che "nuovi modelli" agganci qualsiasi "modelli")
        match = [i for i in interessi if i in testo or all(w in testo for w in i.split() if len(w) > 3)]
        if not match:
            continue
        s = len(match) + 0.5 * liked_tipi[e["tipo"]] + 0.2 * e["rilevanza"]
        cands.append((s, {"id": e["id"], "titolo": a["titolo"], "sintesi": e["sintesi"], "tipo": e["tipo"],
                          "fonte": {"testata": a["testata"], "url": a["url"], "dominio": a["dominio"], "data": a["data"][:10]},
                          "perche": "Per il tuo interesse: " + ", ".join(match)}))
    return [c for _, c in sorted(cands, key=lambda x: -x[0])[:k]]


def render_for(token: str, run: dict) -> dict:
    """Newsletter di un destinatario = nucleo comune ordinato per il suo segmento + sezione personalizzata."""
    r = recipients.BY_TOKEN[token]
    v = run.get("variants", {}).get(recipients.segment_key(r))
    notizie = {n["tema_id"]: n for n in run["core"]["notizie"]}
    ordine = v["ordine"] if v else list(notizie)
    adatt = {a["tema_id"]: a["commento_adattato"] for a in (v or {}).get("adattamenti", [])}
    return {
        "destinatario": r, "oggetto": run["core"]["oggetto"], "editoriale": run["core"]["editoriale"],
        "intro_segmento": (v or {}).get("intro_segmento", ""),
        "notizie": [{**notizie[i], "commento_segmento": adatt.get(i)} for i in ordine if i in notizie],
        "da_portare_in_riunione": run["core"]["da_portare_in_riunione"],
        "personalizzate": personal_section(token, run),
        "dall_editore": DALL_EDITORE,
    }


# slot fisso con i contenuti dell'editore: in produzione arriva dalla sua knowledge base interna (fuori dal perimetro della demo)
DALL_EDITORE = [
    {"titolo": "[Segnaposto] Prossimo evento dell'editore", "nota": "Contenuto da KB interna: calendario eventi"},
    {"titolo": "[Segnaposto] Ultima pubblicazione dell'editore", "nota": "Contenuto da KB interna: pubblicazioni"},
]
