"""Client minimal pour API compatible OpenAI (/chat/completions en streaming)."""
import json
import os
import re
import time

import requests


class LLMError(Exception):
    """kind : "quota" / "overload" / "timeout" (le secours peut prendre le relais), "auth", "other"."""

    def __init__(self, msg, kind="other"):
        super().__init__(msg)
        self.kind = kind


# modèles dont le quota du jour est épuisé (ou introuvables) : (fournisseur, modèle) → instant où réessayer
_exhausted = {}
# modèles retirés par le fournisseur : (fournisseur, modèle) → remplaçant qu'il conseille
_replaced = {}


def _quota_info(text):
    """Quota Gemini : (par jour ?, délai avant nouvel essai en s) tirés des détails de l'erreur 429."""
    per_day, delay = False, None
    try:
        j = json.loads(text)
        j = j[0] if isinstance(j, list) else j
        for d in (j.get("error") or {}).get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in v.get("quotaId", ""):
                    per_day = True
            if d.get("retryDelay"):
                delay = float(str(d["retryDelay"]).rstrip("s"))
    except (ValueError, AttributeError, TypeError):
        pass
    return per_day, delay


def _hours(sec):
    h = round(sec / 3600)
    return f"{h} h" if h >= 1 else f"{max(1, round(sec / 60))} min"


NAMES = {"ollama": "Ollama", "openai": "OpenAI", "gemini": "Gemini", "grok": "Grok", "openrouter": "OpenRouter",
         "groq": "Groq"}
RETRY_STATUS = {429, 500, 502, 503, 504}      # surcharge / limite passagère : on réessaie
RETRY_DELAYS = (1.0, 2.5)                     # court : on est en vol
# « réflexion » minimale des modèles qui raisonnent avant de répondre (Gemini 2.5, GPT-5, Grok mini…) :
# 1 à 3 s de gagnées en vol. Un modèle qui refuse le paramètre est rappelé sans.
# Valeurs essayées dans l'ordre : Gemini 2.5 accepte « none », Gemini 3 refuse de couper toute réflexion
# (400 « invalid argument » sans préciser le paramètre) mais accepte « minimal » / « low ».
FAST_EFFORT = {"gemini": ["none", "minimal", "low"], "openai": ["minimal", "low"], "grok": ["low"],
               "groq": ["low"]}     # gpt-oss : 1er mot en ~0,5 s en « low », ~8 s par défaut
# paramètres acceptés par chaque modèle, appris au premier refus : (fournisseur, modèle) → (effort, température ?)
# Retenus sur disque pour ne pas refaire les essais à chaque lancement.
_ACCEPTED_PATH = os.path.join("data", "llm_params.json")


def _load_accepted():
    try:
        with open(_ACCEPTED_PATH, encoding="utf-8") as f:
            return {tuple(k.split("|", 1)): tuple(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, TypeError):
        return {}


def _save_accepted():
    try:
        os.makedirs(os.path.dirname(_ACCEPTED_PATH), exist_ok=True)
        with open(_ACCEPTED_PATH, "w", encoding="utf-8") as f:
            json.dump({"|".join(k): list(v) for k, v in _accepted.items()}, f, indent=1)
    except OSError:
        pass


_accepted = _load_accepted()

# Suivi de consommation : requêtes du jour par modèle (data/usage.json) et quota restant annoncé par le
# fournisseur dans ses en-têtes (Groq : x-ratelimit-*-requests, quota du jour) : (fournisseur, modèle) → (reste, limite)
USAGE_PATH = os.path.join("data", "usage.json")
QUOTA = {}


def _count_request(prov, model, headers):
    try:
        rem, lim = headers.get("x-ratelimit-remaining-requests"), headers.get("x-ratelimit-limit-requests")
        if rem and lim:
            QUOTA[(prov, model)] = (int(rem), int(lim))
    except ValueError:
        pass
    today = time.strftime("%Y-%m-%d")
    data = usage_today()
    key = f"{prov}|{model}"
    data[key] = data.get(key, 0) + 1
    try:
        os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
        with open(USAGE_PATH, "w", encoding="utf-8") as f:
            json.dump({"date": today, "requests": data}, f, indent=1)
    except OSError:
        pass


def usage_today():
    """{"fournisseur|modèle": requêtes} du jour."""
    try:
        with open(USAGE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return dict(data["requests"]) if data.get("date") == time.strftime("%Y-%m-%d") else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def usage_summary(prov):
    """Ligne pour les réglages : requêtes du jour chez ce fournisseur, et quota restant s'il est connu."""
    used = {k.split("|", 1)[1]: n for k, n in usage_today().items() if k.split("|", 1)[0] == prov}
    if not used and not any(p == prov for p, _m in QUOTA):
        return ""
    parts = [f"Aujourd'hui : {sum(used.values())} requête{'s' * (sum(used.values()) > 1)}"]
    parts += [f"{m} : {rem} sur {lim} restantes" for (p, m), (rem, lim) in QUOTA.items() if p == prov]
    return " · ".join(parts)


def _api_message(text):
    """Extrait le message d'erreur d'une réponse JSON (OpenAI : {"error":…}, Gemini : [{"error":…}])."""
    try:
        j = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    if isinstance(j, list) and j:
        j = j[0]
    e = j.get("error", j) if isinstance(j, dict) else j
    if isinstance(e, dict):
        e = e.get("message") or e
    return str(e).strip()[:200]


class ThinkFilter:
    """Retire les blocs <think>...</think> des modèles de raisonnement (qwen3, deepseek-r1...)."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self.buf = ""
        self.inside = False

    def feed(self, text):
        self.buf += text
        out = []
        while self.buf:
            tag = self.CLOSE if self.inside else self.OPEN
            i = self.buf.find(tag)
            if i >= 0:
                if not self.inside:
                    out.append(self.buf[:i])
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            # garder une éventuelle balise coupée en fin de buffer
            keep = 0
            for n in range(1, len(tag)):
                if self.buf.endswith(tag[:n]):
                    keep = n
            if not self.inside:
                out.append(self.buf[: len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:] if keep else ""
            break
        return "".join(out)

    def flush(self):
        rest = "" if self.inside else self.buf
        self.buf = ""
        return rest


def resolve(cfg):
    """Fusionne le fournisseur actif de cfg["providers"] avec les réglages communs."""
    name = cfg.get("provider", "custom")
    prov = (cfg.get("providers") or {}).get(name)
    if prov is None:
        raise LLMError(f"Fournisseur « {name} » inconnu (choix : {', '.join(cfg.get('providers', {}))})")
    out = {**cfg, **prov, "provider": name}
    if not out.get("api_key") and out.get("api_key_env"):
        out["api_key"] = os.environ.get(out["api_key_env"], "")
    if not out.get("base_url") or not out.get("model"):
        raise LLMError(f"Fournisseur « {name} » : adresse et modèle requis (⚙ Réglages → IA)")
    return out


def needs_key(cfg):
    """Vrai si le fournisseur actif exige une clé API et qu'aucune n'est disponible."""
    try:
        c = resolve(cfg)
    except LLMError:
        return True
    return bool(c.get("api_key_env")) and not c.get("api_key")


class LLMClient:
    def __init__(self, cfg):
        self.cfg = resolve(cfg)
        self.base = self.cfg["base_url"].rstrip("/")
        self.url = self.base + "/chat/completions"
        self.name = NAMES.get(self.cfg["provider"], self.cfg["provider"])
        self.last_model = self.cfg["model"]        # modèle qui a vraiment répondu (secours compris)

    def _error(self, status, text, model):
        detail = _api_message(text)
        if status in (401, 403):
            msg = f"Clé API refusée par {self.name} — vérifie-la dans ⚙ Réglages → IA."
        elif status == 404:
            msg = f"Modèle « {model} » introuvable chez {self.name} — bouton « Lister » dans ⚙ Réglages → IA."
        elif status == 429:
            per_day, delay = _quota_info(text)
            if per_day:
                return (f"Quota gratuit du jour épuisé pour {model} chez {self.name}"
                        + (f" : il revient dans {_hours(delay)}." if delay else "."))
            msg = f"{self.name} : limite de requêtes ou quota atteint."
        elif status >= 500:
            msg = f"{self.name} est surchargé ou indisponible pour le moment (modèle {model})."
        else:
            msg = f"{self.name} a refusé la requête (HTTP {status})."
        return f"{msg}\n{detail}" if detail else msg

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {self.cfg['api_key']}"
        elif self.cfg.get("api_key_env"):
            raise LLMError(f"Clé API manquante pour « {self.cfg['provider']} » : renseigne "
                           f"llm.providers.{self.cfg['provider']}.api_key ou {self.cfg['api_key_env']}")
        return headers

    def models(self):
        """Liste les identifiants de modèles proposés par le fournisseur (GET /models)."""
        try:
            r = requests.get(self.base + "/models", headers=self._headers(), timeout=15)
        except requests.RequestException as e:
            raise LLMError(f"Connexion impossible à {self.base} : {e}") from e
        if r.status_code != 200:
            raise LLMError(self._error(r.status_code, r.text, self.cfg["model"]))
        # Gemini renvoie "models/gemini-…", accepté aussi sans le préfixe
        return sorted(m["id"].removeprefix("models/") for m in r.json().get("data", []))

    def stream(self, messages, on_retry=None, alive=None, fast=False):
        """Génère les morceaux de texte de la réponse au fil de l'eau.

        Surcharge passagère (429/5xx) : nouvel essai, puis modèles de secours (liste séparée par des
        virgules). Quota du jour épuisé : pas de nouvel essai, le modèle est sauté jusqu'à son retour.
        on_retry(message) informe l'utilisateur ; alive() faux = question annulée, on abandonne."""
        on_retry = on_retry or (lambda m: None)
        alive = alive or (lambda: True)
        headers = self._headers()
        prov = self.cfg["provider"]
        models = []
        for m in [self.cfg["model"]] + str(self.cfg.get("fallback_model") or "").split(","):
            m = _replaced.get((prov, m.strip()), m.strip())
            if m and m not in models:
                models.append(m)
        now = time.monotonic()
        usable = [m for m in models if _exhausted.get((prov, m), 0) <= now]
        if not usable:
            raise LLMError(f"Aucun modèle utilisable chez {self.name} pour l'instant ({', '.join(models)} : "
                           f"quota du jour épuisé ou modèle introuvable).", "quota")

        err, kind = None, "overload"
        i = 0
        while i < len(usable):
            model = usable[i]
            if i:
                on_retry(f"{usable[i - 1]} indisponible → modèle de secours {model}")
            i += 1
            for attempt in range(len(RETRY_DELAYS) + 1):
                if not alive():
                    return
                try:
                    r = self._request(model, messages, headers, fast)
                except LLMError as e:
                    if e.kind != "timeout" or i >= len(usable):
                        raise
                    err, kind = str(e), "overload"         # modèle muet : on passe au secours suivant
                    break
                if r.status_code == 200:
                    _count_request(prov, model, r.headers)
                    self.last_model = model
                    yield from self._read(r)
                    return
                status, text = r.status_code, r.text
                r.close()
                err = self._error(status, text, model)
                if status == 404:
                    # modèle retiré ou inconnu : on prend le remplaçant conseillé par le fournisseur
                    # (« use models/gemini-3.5-flash-lite »), sinon le modèle de secours suivant
                    kind = "model"
                    rec = re.search(r"use (?:models/)?([\w.\-]+[\w])", _api_message(text) + " " + text)
                    if rec and rec.group(1) not in usable and rec.group(1) != model:
                        _replaced[(prov, model)] = rec.group(1)
                        usable.insert(i, rec.group(1))
                        print(f"[IA] {model} retiré par {self.name} → {rec.group(1)}", flush=True)
                    else:
                        _exhausted[(prov, model)] = time.monotonic() + 12 * 3600
                    break
                if status not in RETRY_STATUS:
                    raise LLMError(err, "auth" if status in (401, 403) else "other")
                per_day, delay = _quota_info(text) if status == 429 else (False, None)
                wait = float(r.headers.get("Retry-After") or 0) if status == 429 else 0
                if wait > 2 and len(usable) > i:
                    # limite par minute (Groq : jetons/min) : le secours répond tout de suite, ce modèle
                    # revient quand la fenêtre est passée, plutôt que de faire attendre le pilote
                    _exhausted[(prov, model)] = time.monotonic() + wait
                    kind = "quota"
                    print(f"[IA] {model} : limite atteinte, de retour dans {wait:.0f} s → secours", flush=True)
                    break
                if per_day or (delay and delay > 10):
                    # quota épuisé pour longtemps : inutile de réessayer ce modèle
                    _exhausted[(prov, model)] = time.monotonic() + (delay or 3600)
                    kind = "quota"
                    print(f"[IA] {err}", flush=True)
                    break
                if attempt == len(RETRY_DELAYS):
                    break
                delay = min(max(RETRY_DELAYS[attempt], delay or float(r.headers.get("Retry-After") or 0)), 8)
                on_retry(f"{self.name} surchargé, nouvel essai dans {delay:.0f} s…")
                end = time.monotonic() + delay
                while time.monotonic() < end and alive():
                    time.sleep(0.1)
        if len(models) == 1:
            err += "\nAstuce : ajoute des modèles de secours ou un fournisseur de secours dans ⚙ Réglages → IA."
        raise LLMError(err, kind)

    def _request(self, model, messages, headers, fast=False):
        """Requête en streaming. Les paramètres optionnels (réflexion limitée, température) ne sont pas
        acceptés par tous les modèles et le refus (400) ne dit pas toujours lequel : on essaie les
        variantes de la plus rapide à la plus simple, et on retient celle qui passe pour ce modèle."""
        key = (self.cfg["provider"], model)
        efforts = (FAST_EFFORT.get(self.cfg["provider"], []) if fast else []) + [None]
        variants = [(e, True) for e in efforts] + [(None, False)]
        if key in _accepted:
            ok_effort, ok_temp = _accepted[key]
            if not fast:
                ok_effort = None
            variants = [(ok_effort, ok_temp)] + [v for v in variants if v != (ok_effort, ok_temp)]
        r = None
        for effort, with_temp in variants:
            payload = {"model": model, "messages": messages, "stream": True}
            if with_temp:
                payload["temperature"] = self.cfg.get("temperature", 0.3)
            if effort:
                payload["reasoning_effort"] = effort
            payload.update(self.cfg.get("extra") or {})
            if r is not None:
                r.close()
            r = self._post(payload, headers, fast)
            if r.status_code != 400:
                if r.status_code == 200 and (fast or key not in _accepted) and _accepted.get(key) != (effort, with_temp):
                    _accepted[key] = (effort, with_temp)
                    _save_accepted()
                return r
        return r

    def _read(self, r):
        try:
            yield from self._lines(r)
        except requests.RequestException as e:      # Wi-Fi / 4G coupé pendant la réponse
            raise LLMError(f"Connexion à {self.name} coupée pendant la réponse. Réessaie.") from e

    def _lines(self, r):
        think = ThinkFilter()
        with r:
            for raw in r.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if "error" in chunk:
                    raise LLMError(f"{self.name} : {_api_message(data)}")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if delta:
                    text = think.feed(delta)
                    if text:
                        yield text
        tail = think.flush()
        if tail:
            yield tail

    def _read_timeout(self, fast):
        """Attente maximale entre deux morceaux de réponse : en vol (fast), au plus 45 s, pour que le secours
        prenne vite le relais si le service se bloque ; sinon le « Délai maximum » des réglages."""
        timeout = float(self.cfg.get("timeout", 120))
        return min(timeout, 45) if fast else timeout

    def _post(self, payload, headers, fast=False):
        try:
            return requests.post(self.url, json=payload, headers=headers, stream=True,
                                 timeout=(10, self._read_timeout(fast)))
        except requests.Timeout as e:
            raise LLMError(f"{self.name} ne répond pas (délai dépassé). Réessaie, ou augmente le délai "
                           f"dans ⚙ Réglages → IA.", "timeout") from e
        except requests.ConnectionError as e:
            hint = " Ollama est-il lancé ?" if self.cfg["provider"] == "ollama" else " Vérifie ta connexion Internet."
            raise LLMError(f"Impossible de joindre {self.name} ({self.base}).{hint}") from e
        except requests.RequestException as e:
            raise LLMError(f"Connexion impossible à {self.name} : {e}") from e
