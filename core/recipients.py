"""Destinatari di esempio (profili fittizi) e segmenti.

Il token è il link personale al pannello: nessun login, lo standard dei "preference center" degli ESP.
In produzione il token è casuale (secrets.token_urlsafe) e ruotabile; qui è fisso per rendere la demo riproducibile.
"""

RECIPIENTS = [
    {
        "token": "pa-7k2m9x",
        "ruolo": "Responsabile Transizione Digitale",
        "organizzazione": "Comune capoluogo (profilo fittizio)",
        "settore": "pubblica amministrazione",
        "maturita": "esplorativa",
        "dimensione": "PA locale",
        "interessi": ["procurement", "ai act", "servizi ai cittadini"],
    },
    {
        "token": "mfg-4q8w1z",
        "ruolo": "CIO",
        "organizzazione": "Gruppo manifatturiero, 1.200 dipendenti (profilo fittizio)",
        "settore": "manifattura",
        "maturita": "in_scala",
        "dimensione": "grande impresa",
        "interessi": ["logistica", "manutenzione predittiva", "supply chain"],
    },
    {
        "token": "fsh-9p3n6v",
        "ruolo": "Head of AI",
        "organizzazione": "Brand moda, 400 dipendenti (profilo fittizio)",
        "settore": "moda e retail",
        "maturita": "sperimentale",
        "dimensione": "media impresa",
        "interessi": ["moda", "retail", "nuovi modelli"],
    },
]

BY_TOKEN = {r["token"]: r for r in RECIPIENTS}


def segment_key(r: dict) -> str:
    """Le varianti si generano per segmento (settore × maturità × dimensione), non per persona."""
    return f"{r['settore']}|{r['maturita']}|{r['dimensione']}"


def segments() -> dict[str, dict]:
    out = {}
    for r in RECIPIENTS:
        out.setdefault(segment_key(r), {k: r[k] for k in ("settore", "maturita", "dimensione", "ruolo")})
    return out
