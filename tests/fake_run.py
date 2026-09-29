"""Test di cablaggio senza LLM: sostituisce llm.structured con risposte finte ma coerenti con gli schemi
e con gli id reali, poi esegue make_replay con articoli veri da Google News."""
import os, sys, random
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); os.environ["REPLAY_PATH"] = str(ROOT / "data" / "fake_replay.json")
from core import llm, models, pipeline
from scripts import make_replay

def fake(system, user, schema, temperature=0.2, **kw):
    import json, re
    if schema is models.QueryPlan:
        qs = [("AI Act obblighi imprese","regolazione","it"),("intelligenza artificiale pubblica amministrazione","governance","it"),
              ("AI adoption enterprise survey","strategia","en"),("investimenti intelligenza artificiale Italia","investimenti","it"),
              ("competenze IA lavoratori","competenze","it"),("generative AI manufacturing case study","casi_uso","en"),
              ("nuovo modello linguistico open","tecnologia","it"),("AI fashion retail","casi_uso","en")]
        return models.QueryPlan(queries=[{"query":q,"tema":t,"lingua":l} for q,t,l in qs]), {"model":"fake","attempts":["fake: ok"]}
    if schema is models.ExtractBatch:
        ids = re.findall(r'"id": "(a[0-9a-f]+)"', user)
        tipi = ["regolazione","caso_uso","investimento","prodotto","competenze","policy_pubblica"]
        return models.ExtractBatch(items=[{"id":i,"tema":"governance","tipo":random.choice(tipi),"settori":["manifattura"],
            "tags":["logistica","moda","ai act"][:random.randint(1,3)],"rilevanza":random.randint(2,5),"motivazione":"test",
            "hype":random.randint(1,4),"sintesi":"Sintesi di test."} for i in ids]), {"model":"fake","attempts":["fake: ok"]}
    if schema is models.ClusterResult:
        ids = re.findall(r'"id": "(a[0-9a-f]+)"', user)
        return models.ClusterResult(temi=[{"titolo":f"Tema {n}","articoli":ids[n*2:n*2+2]} for n in range((len(ids)+1)//2)]), {"model":"fake","attempts":["fake: ok"]}
    if schema is models.NewsletterCore:
        blocks = re.findall(r'"tema_id": "(t\d+)".*?"id": "(a[0-9a-f]+)"', user)
        return models.NewsletterCore(oggetto="Test", editoriale="parola "*120, notizie=[{"tema_id":t,"titolo":"Titolo "+t,
            "fatto":"parola "*60,"commento":"parola "*150,"fonti_ids":[a,"a_inventato"]} for t,a in blocks],
            da_portare_in_riunione=["uno","due","tre"]), {"model":"fake","attempts":["fake: JSON non valido (tentativo 1)","fake: ok (tentativo 2)"]}
    if schema is models.SegmentVariant:
        ids = re.findall(r'"tema_id": "(t\d+)"', user)
        return models.SegmentVariant(intro_segmento="Intro", ordine=list(reversed(ids)),
            adattamenti=[{"tema_id":i,"commento_adattato":"Adattato "+i} for i in ids]), {"model":"fake","attempts":["fake: ok"]}
    raise AssertionError(schema)

llm.structured = fake
make_replay.main()
