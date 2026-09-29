"""Storage chiave-valore minimale: Upstash Redis (REST) su Vercel, SQLite in locale.

Contiene solo ciò che deve sopravvivere tra le richieste: run live, feedback e preferenze dei destinatari,
contatori delle quote. Il run di replay è un file statico versionato (public/replay.json).
"""
import json
import sqlite3
import threading
import time

import httpx

from . import config


class _Upstash:
    def _cmd(self, *args):
        r = httpx.post(config.KV_URL, json=list(args), headers={"Authorization": f"Bearer {config.KV_TOKEN}"}, timeout=10)
        r.raise_for_status()
        return r.json().get("result")

    def get(self, key):
        v = self._cmd("GET", key)
        return json.loads(v) if v is not None else None

    def set(self, key, value):
        self._cmd("SET", key, json.dumps(value, ensure_ascii=False))

    def incr(self, key, ttl):
        n = self._cmd("INCR", key)
        if n == 1:
            self._cmd("EXPIRE", key, ttl)
        return n

    def push(self, key, value):
        self._cmd("RPUSH", key, json.dumps(value, ensure_ascii=False))

    def items(self, key):
        return [json.loads(v) for v in self._cmd("LRANGE", key, 0, -1) or []]


class _Sqlite:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists kv(k text primary key, v text, exp real)")
        self.db.execute("create table if not exists lst(id integer primary key autoincrement, k text, v text)")
        self.db.commit()

    def get(self, key):
        row = self.db.execute("select v, exp from kv where k=?", (key,)).fetchone()
        if not row or (row[1] and row[1] < time.time()):
            return None
        return json.loads(row[0])

    def set(self, key, value, exp=None):
        with self.lock:
            self.db.execute("insert or replace into kv values(?,?,?)", (key, json.dumps(value, ensure_ascii=False), exp))
            self.db.commit()

    def incr(self, key, ttl):
        with self.lock:
            n = (self.get(key) or 0) + 1
            row = self.db.execute("select exp from kv where k=?", (key,)).fetchone()
            exp = row[0] if row and row[0] and row[0] > time.time() else time.time() + ttl
            self.db.execute("insert or replace into kv values(?,?,?)", (key, json.dumps(n), exp))
            self.db.commit()
            return n

    def push(self, key, value):
        with self.lock:
            self.db.execute("insert into lst(k, v) values(?,?)", (key, json.dumps(value, ensure_ascii=False)))
            self.db.commit()

    def items(self, key):
        return [json.loads(r[0]) for r in self.db.execute("select v from lst where k=? order by id", (key,))]


kv = _Upstash() if config.KV_URL and config.KV_TOKEN else _Sqlite(config.SQLITE_PATH)
BACKEND = "upstash" if isinstance(kv, _Upstash) else "sqlite"
