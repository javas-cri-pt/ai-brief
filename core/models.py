"""Schemi Pydantic: sono il contratto tra i passaggi della pipeline e il validatore dell'output LLM."""
from typing import Literal
from pydantic import BaseModel, Field, conint

Tema = Literal["strategia", "governance", "casi_uso", "regolazione", "competenze", "investimenti", "tecnologia"]
Tipo = Literal["regolazione", "caso_uso", "investimento", "prodotto", "ricerca", "competenze", "policy_pubblica"]


# --- A.1 passo 1: pianificazione delle query ---
class Query(BaseModel):
    query: str = Field(description="query di ricerca breve, 2-6 parole")
    tema: Tema
    lingua: Literal["it", "en"]


class QueryPlan(BaseModel):
    queries: list[Query] = Field(min_length=6, max_length=12)


# --- A.1 passo 4: estrazione strutturata per articolo ---
class ArticleEval(BaseModel):
    id: str
    tema: Tema
    tipo: Tipo
    settori: list[str] = Field(default_factory=list, description="settori toccati, es. sanità, PA, manifattura, finanza, moda, logistica")
    tags: list[str] = Field(default_factory=list, description="3-5 parole chiave minuscole")
    rilevanza: conint(ge=1, le=5)
    motivazione: str = Field(description="perché è (o non è) rilevante per un AI decision maker, max 1 frase")
    hype: conint(ge=1, le=5) = Field(description="1 = fatto concreto e verificabile, 5 = annuncio di marketing senza sostanza")
    sintesi: str = Field(description="il fatto in una frase, senza aggettivi")


class ExtractBatch(BaseModel):
    items: list[ArticleEval]


# --- A.1 passo 5: raggruppamento in temi ---
class Cluster(BaseModel):
    titolo: str = Field(description="titolo del tema, max 10 parole, fattuale")
    articoli: list[str] = Field(min_length=1, description="id degli articoli del tema")


class ClusterResult(BaseModel):
    temi: list[Cluster]


# --- A.1 passo 7 + A.2: redazione ---
class NewsItem(BaseModel):
    tema_id: str
    titolo: str
    fatto: str = Field(description="sintesi fattuale in 3-4 righe, solo informazioni presenti negli articoli")
    commento: str = Field(description="commento editoriale: perché conta per un AI decision maker")
    fonti_ids: list[str] = Field(min_length=1, description="id degli articoli su cui si basa il fatto")


class NewsletterCore(BaseModel):
    oggetto: str
    editoriale: str
    notizie: list[NewsItem] = Field(min_length=3, max_length=7)
    da_portare_in_riunione: list[str] = Field(min_length=3, max_length=3)


# --- A.3: adattamento per segmento ---
class Adattamento(BaseModel):
    tema_id: str
    commento_adattato: str


class SegmentVariant(BaseModel):
    intro_segmento: str = Field(description="1-2 frasi che collegano l'uscita al contesto del segmento")
    ordine: list[str] = Field(description="tema_id delle notizie nell'ordine più utile per il segmento")
    adattamenti: list[Adattamento]
