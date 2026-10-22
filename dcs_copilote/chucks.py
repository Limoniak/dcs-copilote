"""Chuck's Guides (chucksguides.com) : téléchargement et mise à jour automatique des guides PDF.

Les guides sont gros (jusqu'à ~150 Mo) : on ne prend que les appareils choisis (par défaut, les modules
installés) et on vérifie au plus une fois par semaine, par une simple requête HEAD (ETag) — un guide
n'est retéléchargé que s'il a changé.
"""
import json
import os
import re
import time
from urllib.parse import quote, unquote

import requests

SITE = "https://chucksguides.com"
DATA_DIR = os.path.join("data", "chucks")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CHECK_EVERY = 7 * 86400
HEADERS = {"User-Agent": "DCS-Copilote/1.0 (mise a jour hebdomadaire des guides, usage personnel)"}
# guides dont le nom ne ressemble pas au dossier du module dans Mods/aircraft
ALIASES = {"dh98": ["mosquito"], "spitfire-lf-mk-ix": ["spitfire"], "l-39za": ["l39"], "c-101cc": ["c101"]}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"guides": {}, "files": {}, "last_check": 0}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=30, **kw)
    r.raise_for_status()
    return r


def list_guides():
    """{slug: nom} des guides DCS publiés sur le site."""
    html = _get(SITE + "/").text
    out = {}
    for m in re.finditer(r'<a[^>]*href="/aircraft/dcs/([^"]+)"[^>]*class="card"[^>]*>(.*?)</a>'
                         r'|<a[^>]*class="card"[^>]*href="/aircraft/dcs/([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        slug = m.group(1) or m.group(3)
        name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2) or m.group(4))).strip()
        out[slug] = name or slug
    if not out:
        raise RuntimeError("liste des guides introuvable (le site a peut-être changé)")
    return out


def pdf_url(slug):
    html = _get(f"{SITE}/aircraft/dcs/{slug}").text
    m = re.search(r'href="(https://assets\.chucksguides\.com/[^"]+?\.pdf)"', html, re.I)
    if not m:
        raise RuntimeError(f"PDF introuvable sur la page {slug}")
    url = m.group(1)
    return url if "%" in url else quote(url, safe=":/")


def installed_slugs(guides, dcs_dir):
    """Guides correspondant aux modules installés (dossiers de Mods/aircraft)."""
    try:
        folders = [_norm(f) for f in os.listdir(os.path.join(dcs_dir, "Mods", "aircraft"))]
    except (OSError, TypeError):
        return []
    out = []
    for slug in guides:
        keys = [_norm(slug)] + ALIASES.get(slug, [])
        for k in keys:
            if any(len(min(k, f, key=len)) >= 3 and (f.startswith(k) or k.startswith(f)) for f in folders):
                out.append(slug)
                break
    return out


def _cleanup_parts():
    for f in os.listdir(DATA_DIR):
        if f.endswith(".part"):
            try:
                os.remove(os.path.join(DATA_DIR, f))
            except OSError:
                pass


def slug_for(aircraft, guides):
    """Guide de Chuck correspondant à l'appareil piloté (nom lisible ou nom interne DCS), ou None."""
    from .aircraft import AIRCRAFT
    if not aircraft:
        return None
    keys = {_norm(aircraft), _norm(aircraft.split()[0])}
    keys |= {_norm(k) for k, v in AIRCRAFT.items() if v == aircraft or k == aircraft}
    keys |= {_norm(k.split("_")[0]) for k, v in AIRCRAFT.items() if v == aircraft or k == aircraft}
    keys = {k for k in keys if len(k) >= 3}
    best, best_len = None, 0
    for slug, name in guides.items():
        if _norm(name) in keys:
            return slug                         # même nom que sur le site
        for s in [_norm(slug)] + ALIASES.get(slug, []):
            for k in keys:
                if k.startswith(s) or s.startswith(k):
                    n = min(len(s), len(k))
                    if n > best_len:
                        best, best_len = slug, n
    return best


def due(state):
    return time.time() - state.get("last_check", 0) > CHECK_EVERY


def update(slugs, progress=lambda msg, frac=None: None, should_stop=lambda: False):
    """Télécharge les guides nouveaux ou modifiés. Renvoie (mis_à_jour, erreurs) : listes de noms."""
    state = load_state()
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        state["guides"] = list_guides()
    except Exception as e:  # noqa: BLE001
        if not state["guides"]:
            raise
        progress(f"Liste des guides indisponible ({e}), on garde la précédente")
    updated, errors = [], []
    for i, slug in enumerate(slugs):
        if should_stop():
            break
        name = state["guides"].get(slug, slug)
        progress(f"Vérification : {name}", i / max(1, len(slugs)))
        try:
            url = pdf_url(slug)
            head = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
            head.raise_for_status()
            tag = head.headers.get("ETag") or head.headers.get("Last-Modified") or head.headers.get("Content-Length")
            fname = os.path.basename(unquote(url))
            path = os.path.join(DATA_DIR, fname)
            known = state["files"].get(slug, {})
            if known.get("tag") == tag and os.path.exists(path):
                continue
            size = int(head.headers.get("Content-Length") or 0)
            with _get(url, stream=True) as r, open(path + ".part", "wb") as f:
                got = 0
                for chunk in r.iter_content(1 << 16):
                    if should_stop():
                        raise InterruptedError("arrêté")
                    f.write(chunk)
                    got += len(chunk)
                    if size:
                        progress(f"Téléchargement : {name} ({got >> 20} / {size >> 20} Mo)",
                                 (i + got / size) / len(slugs))
            os.replace(path + ".part", path)
            old = known.get("file")
            if old and old != fname and os.path.exists(os.path.join(DATA_DIR, old)):
                os.remove(os.path.join(DATA_DIR, old))      # le guide a changé de nom de fichier
            state["files"][slug] = {"tag": tag, "file": fname, "date": time.strftime("%Y-%m-%d")}
            save_state(state)
            updated.append(name)
        except InterruptedError:
            _cleanup_parts()
            break
        except Exception as e:  # noqa: BLE001
            _cleanup_parts()
            errors.append(f"{name} : {e}")
    # guides désélectionnés : on libère la place
    for slug in list(state["files"]):
        if slug not in slugs:
            f = os.path.join(DATA_DIR, state["files"][slug]["file"])
            if os.path.exists(f):
                os.remove(f)
            del state["files"][slug]
    if not should_stop():
        state["last_check"] = time.time()
    save_state(state)
    return updated, errors
