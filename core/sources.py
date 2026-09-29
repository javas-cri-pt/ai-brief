"""Recupero articoli e affidabilità delle fonti.

Demo: le query generate dall'agente vengono eseguite su Google News RSS (gratuito, senza chiave).
I termini del feed ne consentono l'uso personale e non commerciale: in produzione servono feed diretti
delle fonti o un'API di news con licenza.
"""
import hashlib
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from . import config

# Prior di affidabilità per il cold start: nessun lettore ha ancora votato, quindi partiamo da un giudizio
# redazionale (fatto da noi) su istituzioni e testate di riferimento. Il feedback dei lettori lo aggiorna.
TRUST_PRIOR = {
    # istituzioni e regolatori
    "europa.eu": 0.95, "ec.europa.eu": 0.95, "digital-strategy.ec.europa.eu": 0.95, "europarl.europa.eu": 0.95,
    "agid.gov.it": 0.95, "acn.gov.it": 0.95, "garanteprivacy.it": 0.95, "governo.it": 0.9, "innovazione.gov.it": 0.9,
    "oecd.org": 0.9, "bancaditalia.it": 0.9, "istat.it": 0.9,
    # testate di riferimento
    "ilsole24ore.com": 0.85, "corriere.it": 0.8, "repubblica.it": 0.75, "ansa.it": 0.8, "reuters.com": 0.9,
    "ft.com": 0.9, "economist.com": 0.85, "bloomberg.com": 0.85, "agendadigitale.eu": 0.8, "corrierecomunicazioni.it": 0.8,
    "wired.it": 0.7, "zerounoweb.it": 0.75, "cybersecurity360.it": 0.75, "ai4business.it": 0.7, "forumpa.it": 0.8,
    "technologyreview.com": 0.85, "theverge.com": 0.7, "techcrunch.com": 0.7,
}
DEFAULT_TRUST = 0.5


def domain_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def prior_trust(domain: str) -> float:
    parts = domain.split(".")
    for i in range(len(parts) - 1):  # sottodomini: news.ilsole24ore.com -> ilsole24ore.com
        d = ".".join(parts[i:])
        if d in TRUST_PRIOR:
            return TRUST_PRIOR[d]
    return DEFAULT_TRUST


def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _search(q: str, lang: str) -> list[dict]:
    loc = {"it": ("it", "IT", "IT:it"), "en": ("en", "US", "US:en")}[lang]
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{config.WINDOW_DAYS}d")
           + f"&hl={loc[0]}&gl={loc[1]}&ceid={loc[2]}")
    try:
        root = ET.fromstring(httpx.get(url, timeout=15, follow_redirects=True).content)
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        src = item.find("source")
        src_url = src.get("url", "") if src is not None else ""
        testata = (src.text or "").strip() if src is not None else ""
        title = _clean(item.findtext("title", ""))
        if testata and title.endswith(" - " + testata):
            title = title[: -len(" - " + testata)]
        try:
            pub = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(timezone.utc)
        except Exception:
            continue
        out.append({
            "titolo": title,
            "testata": testata,
            "dominio": domain_of(src_url),
            "url": item.findtext("link", ""),
            "data": pub.isoformat(),
            "estratto": _clean(item.findtext("description", ""))[:500],
            "query": q,
        })
    return out[:6]  # i primi risultati per query: bilancia la copertura dei temi


def fetch(queries: list[dict], trust: dict[str, float]) -> list[dict]:
    """Esegue le query, poi pulizia deterministica: finestra temporale, dedup per URL e titolo, tetto."""
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda q: _search(q["query"], q.get("lingua", "it")), queries))
    since = datetime.now(timezone.utc) - timedelta(days=config.WINDOW_DAYS)
    seen_url, seen_title, articles = set(), set(), []
    # interleave: un risultato per query a turno, così nessuna query monopolizza il tetto
    for rank in range(max((len(r) for r in results), default=0)):
        for res in results:
            if rank >= len(res):
                continue
            a = res[rank]
            key_title = re.sub(r"\W+", "", a["titolo"].lower())[:60]
            if a["url"] in seen_url or key_title in seen_title or datetime.fromisoformat(a["data"]) < since:
                continue
            seen_url.add(a["url"]); seen_title.add(key_title)
            a["id"] = "a" + hashlib.sha1(a["url"].encode()).hexdigest()[:7]
            a["affidabilita"] = round(trust.get(a["dominio"], prior_trust(a["dominio"])), 2)
            articles.append(a)
    return articles[: config.MAX_ARTICLES]
