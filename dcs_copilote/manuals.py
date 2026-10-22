"""Manuels des modules DCS : indexation des PDF et recherche des passages utiles pour une question.

Les questions sont en français, les manuels en anglais : la recherche est sémantique et multilingue
(petit modèle d'embeddings local), combinée à une recherche par mots-clés pour les termes exacts
(« TMS », « ICLS »…). Index stocké dans data/manuals/ ; modèle dans models/.
"""
import glob
import json
import math
import os
import re
import sys
import threading
from collections import Counter

import numpy as np

INDEX_DIR = os.path.join("data", "manuals")
MODEL_DIR = "models"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
CHUNK_CHARS, CHUNK_OVERLAP = 900, 150
MIN_SCORE = 0.32          # en dessous, le passage est jugé hors sujet
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]+")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tokens(text):
    return [w.lower() for w in _WORD.findall(text)]


# --------------------------------------------------------------------------- emplacements
def dcs_install_candidates():
    """Dossiers d'installation de DCS World trouvés (standalone ou Steam, Windows ou Linux/Proton)."""
    roots = []
    if sys.platform == "win32":
        for drive in "CDEFG":
            roots += [f"{drive}:/Program Files/Eagle Dynamics/DCS World",
                      f"{drive}:/Program Files/Eagle Dynamics/DCS World OpenBeta",
                      f"{drive}:/Eagle Dynamics/DCS World",
                      f"{drive}:/DCS World",
                      f"{drive}:/Program Files (x86)/Steam/steamapps/common/DCSWorld",
                      f"{drive}:/SteamLibrary/steamapps/common/DCSWorld"]
    home = os.path.expanduser("~")
    steams = [os.path.join(home, ".steam", "steam"), os.path.join(home, ".local", "share", "Steam"),
              os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
              "C:/Program Files (x86)/Steam"]
    for s in steams:
        libs = [s]
        vdf = os.path.join(s, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libs += [p.replace("\\\\", "/") for p in re.findall(r'"path"\s+"([^"]+)"', f.read())]
        except OSError:
            pass
        roots += [os.path.join(lib, "steamapps", "common", "DCSWorld") for lib in libs]
    out = []
    for r in roots:
        if os.path.isdir(os.path.join(r, "Mods", "aircraft")) and os.path.realpath(r) not in map(os.path.realpath, out):
            out.append(r)
    return out


def find_pdfs(dcs_dir, extra_dir, chucks=True):
    """[(chemin, module)] : manuels officiels (Mods/aircraft/<module>/Doc), Chuck's Guides téléchargés
    et PDF du dossier perso."""
    out = []
    if chucks:
        from .chucks import DATA_DIR
        for p in glob.glob(os.path.join(DATA_DIR, "*.pdf")):
            out.append((p, os.path.splitext(os.path.basename(p))[0]))
    if dcs_dir:
        for p in glob.glob(os.path.join(dcs_dir, "Mods", "aircraft", "*", "Doc", "**", "*.pdf"), recursive=True):
            module = os.path.relpath(p, os.path.join(dcs_dir, "Mods", "aircraft")).split(os.sep)[0]
            out.append((p, module))
    if extra_dir:
        for p in glob.glob(os.path.join(extra_dir, "**", "*.pdf"), recursive=True):
            out.append((p, os.path.splitext(os.path.basename(p))[0]))
    return sorted(set(out))


# --------------------------------------------------------------------------- indexation
def _chunks(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    if len(text) <= CHUNK_CHARS:
        return [text]
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):                      # couper sur une fin de phrase ou de ligne si possible
            cut = max(text.rfind(". ", start + CHUNK_CHARS // 2, end), text.rfind("\n", start + CHUNK_CHARS // 2, end))
            if cut > 0:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return out


def _embedder(threads=None):
    from fastembed import TextEmbedding
    os.makedirs(MODEL_DIR, exist_ok=True)
    return TextEmbedding(EMBED_MODEL, cache_dir=MODEL_DIR, threads=threads)


def _doc_key(path):
    st = os.stat(path)
    return f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"


def _load_raw():
    try:
        with open(os.path.join(INDEX_DIR, "chunks.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data, np.load(os.path.join(INDEX_DIR, "emb.npy"))
    except (OSError, ValueError):
        return None, None


def build_index(pdfs, progress=lambda done, total, msg: None, should_stop=lambda: False, threads=None):
    """Extrait, découpe et vectorise les PDF. Incrémental : un PDF inchangé depuis la dernière
    indexation est repris tel quel, seuls les nouveaux ou modifiés sont analysés. Renvoie un résumé."""
    import pymupdf
    old, old_emb = _load_raw()
    reuse = {}
    if old and old.get("model") == EMBED_MODEL:
        rows = {}
        for i, c in enumerate(old["chunks"]):
            rows.setdefault(c["doc"], []).append(i)
        reuse = {d["key"]: (d, rows.get(di, [])) for di, d in enumerate(old["docs"]) if d.get("key")}

    chunks, rows, docs = [], [], []           # rows[i] : vecteur repris, ou None à calculer
    for i, (path, module) in enumerate(pdfs):
        if should_stop():
            raise InterruptedError("indexation annulée")
        try:
            key = _doc_key(path)
        except OSError:
            continue
        if key in reuse:
            d, idx = reuse[key]
            for j in idx:
                chunks.append(dict(old["chunks"][j], doc=len(docs)))
                rows.append(old_emb[j])
            docs.append(dict(d, module=module))
            continue
        progress(i, len(pdfs), f"Lecture de {os.path.basename(path)}…")
        try:
            with pymupdf.open(path) as doc:
                n0 = len(chunks)
                for pno, page in enumerate(doc, 1):
                    text = page.get_text("text")
                    if len(text.strip()) < 80:
                        continue
                    for c in _chunks(text):
                        if len(c) >= 80:
                            chunks.append({"doc": len(docs), "page": pno, "text": c})
                            rows.append(None)
                docs.append({"path": path, "name": os.path.basename(path), "module": module,
                             "chunks": len(chunks) - n0, "key": key})
        except Exception as e:  # noqa: BLE001
            print(f"[manuels] {path} illisible : {e}", flush=True)
    if not chunks:
        raise ValueError("aucun texte trouvé dans les PDF")

    todo = [i for i, r in enumerate(rows) if r is None]
    if todo:
        progress(0, len(todo), "Chargement du modèle de recherche (1er lancement : ~220 Mo)…")
        model = _embedder(threads)
        batch = 64
        for s in range(0, len(todo), batch):
            if should_stop():
                raise InterruptedError("indexation annulée")
            part = todo[s:s + batch]
            for i, v in zip(part, model.embed([chunks[i]["text"] for i in part], batch_size=batch)):
                v = np.asarray(v, dtype=np.float32)
                rows[i] = v / (np.linalg.norm(v) + 1e-9)
            progress(min(s + batch, len(todo)), len(todo), "Analyse des manuels…")
    emb = np.asarray(rows, dtype=np.float16)

    os.makedirs(INDEX_DIR, exist_ok=True)
    np.save(os.path.join(INDEX_DIR, "emb.npy"), emb)
    with open(os.path.join(INDEX_DIR, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump({"model": EMBED_MODEL, "docs": docs, "chunks": chunks}, f, ensure_ascii=False)
    out = summary_of(docs, len(chunks))
    out["new_chunks"] = len(todo)
    return out


def summary_of(docs, n_chunks):
    per = Counter(d["module"] for d in docs if d["chunks"])
    return {"docs": sum(1 for d in docs if d["chunks"]), "chunks": n_chunks, "modules": dict(per)}


# --------------------------------------------------------------------------- recherche
class Manuals:
    """Index chargé en mémoire ; search() est appelable depuis le thread de travail."""

    def __init__(self, log=print):
        self.log = log
        self.ready = False
        self._lock = threading.Lock()
        self.model = None

    def load(self):
        """Charge l'index et le modèle (quelques secondes) ; False s'il n'y a pas d'index."""
        path = os.path.join(INDEX_DIR, "chunks.json")
        if not os.path.exists(path):
            self.ready = False
            return False
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        emb = np.load(os.path.join(INDEX_DIR, "emb.npy")).astype(np.float32)
        model = _embedder()
        # index mots-clés (BM25)
        tfs, df = [], Counter()
        for c in data["chunks"]:
            tf = Counter(_tokens(c["text"]))
            tfs.append(tf)
            df.update(tf.keys())
        with self._lock:
            self.docs, self.chunks, self.emb, self.model = data["docs"], data["chunks"], emb, model
            self.tfs, self.df = tfs, df
            self.avg_len = sum(sum(t.values()) for t in tfs) / max(1, len(tfs))
            self.doc_of = np.array([c["doc"] for c in self.chunks])
            self.ready = True
        return True

    def summary(self):
        return summary_of(self.docs, len(self.chunks)) if self.ready else None

    # -------- correspondance appareil ↔ manuels
    def _docs_for(self, aircraft):
        """Indices des documents du module piloté (None = tous)."""
        if not aircraft:
            return None
        from .aircraft import AIRCRAFT
        keys = {_norm(aircraft), _norm(aircraft.split()[0])}
        keys |= {_norm(k.split("_")[0]) for k, v in AIRCRAFT.items() if v == aircraft}
        keys |= {_norm(k) for k, v in AIRCRAFT.items() if v == aircraft}
        keys = {k for k in keys if len(k) >= 3}
        hits = [i for i, d in enumerate(self.docs)
                if any(k in _norm(d["module"]) or _norm(d["module"]) in k for k in keys)]
        return hits or []

    def _bm25(self, query_tokens, idx):
        n, k1, b = len(self.tfs), 1.4, 0.75
        out = np.zeros(len(idx), dtype=np.float32)
        for qt in set(query_tokens):
            df = self.df.get(qt)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for j, i in enumerate(idx):
                tf = self.tfs[i].get(qt)
                if tf:
                    dl = sum(self.tfs[i].values())
                    out[j] += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / self.avg_len))
        return out

    def search(self, query, aircraft=None, k=4):
        """[(score, chunk, doc)] les plus pertinents pour la question (vide si rien de fiable)."""
        if not self.ready or not query.strip():
            return []
        with self._lock:
            docs = self._docs_for(aircraft)
            if docs == []:
                return []                       # appareil connu mais aucun manuel indexé pour lui
            mask = np.ones(len(self.chunks), bool) if docs is None else np.isin(self.doc_of, docs)
            idx = np.flatnonzero(mask)
            q = np.asarray(next(iter(self.model.query_embed([query]))), dtype=np.float32)
            q /= np.linalg.norm(q) + 1e-9
            sem = self.emb[idx] @ q
            # pré-sélection sémantique, puis bonus mots-clés pour les termes exacts du cockpit
            top = np.argsort(-sem)[:60]
            kw = self._bm25(_tokens(query), idx[top])
            if kw.max() > 0:
                kw = kw / kw.max()
            score = sem[top] + 0.15 * kw
            order = np.argsort(-score)
            out = []
            best = sem[top[order[0]]]
            for j in order[:k]:
                # hors sujet, ou nettement moins pertinent que le meilleur passage
                if sem[top[j]] < MIN_SCORE or sem[top[j]] < best - 0.12:
                    continue
                c = self.chunks[idx[top[j]]]
                out.append((float(score[j]), c, self.docs[c["doc"]]))
            return out


def format_context(results, max_chars=3600):
    """Bloc à insérer dans le prompt système."""
    parts, total = [], 0
    for n, (_s, c, d) in enumerate(results, 1):
        text = c["text"][: max_chars - total]
        if not text:
            break
        parts.append(f"[{n}] {d['name']}, page {c['page']} :\n{text}")
        total += len(text)
    return "\n\n".join(parts)


def sources_line(results):
    seen = []
    for _s, c, d in results:
        name = re.sub(r"\.pdf$", "", d["name"], flags=re.I)
        item = f"{name} p. {c['page']}"
        if item not in seen:
            seen.append(item)
    return "Sources : " + " · ".join(seen) if seen else ""
