"""Client minimal pour API compatible OpenAI (/chat/completions en streaming)."""
import json
import os
import re
import time

import requests


class LLMError(Exception):
    """kind : "quota" / "overload" (le secours peut prendre le relais), "auth", "other"."""

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
FAST_EFFORT = {"gemini": "none", "openai": "minimal", "grok": "low"}


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
                r = self._request(model, messages, headers, fast)
                if r.status_code == 200:
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
                        print(f"[IA] {model} retiré par {self.name} → {rec.group(1)} (à mettre comme modèle "
                              f"dans ⚙ Réglages → IA)", flush=True)
                    else:
                        _exhausted[(prov, model)] = time.monotonic() + 12 * 3600
                    break
                if status not in RETRY_STATUS:
                    raise LLMError(err, "auth" if status in (401, 403) else "other")
                per_day, delay = _quota_info(text) if status == 429 else (False, None)
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
        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.cfg.get("temperature", 0.3),
            "stream": True,
        }
        effort = FAST_EFFORT.get(self.cfg["provider"]) if fast else None
        if effort:
            payload["reasoning_effort"] = effort
        payload.update(self.cfg.get("extra") or {})
        r = self._post(payload, headers)
        # paramètres optionnels refusés par certains modèles (température des modèles de raisonnement
        # OpenAI, reasoning_effort des modèles sans réflexion) : on les retire et on rappelle
        for key in ("reasoning_effort", "temperature"):
            if r.status_code == 400 and key in payload and key.split("_")[0] in r.text:
                r.close()
                payload.pop(key)
                r = self._post(payload, headers)
        return r

    def _read(self, r):
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

    def _post(self, payload, headers):
        try:
            return requests.post(self.url, json=payload, headers=headers, stream=True,
                                 timeout=(10, self.cfg.get("timeout", 120)))
        except requests.Timeout as e:
            raise LLMError(f"{self.name} ne répond pas (délai dépassé). Réessaie, ou augmente le délai "
                           f"dans ⚙ Réglages → IA.") from e
        except requests.ConnectionError as e:
            hint = " Ollama est-il lancé ?" if self.cfg["provider"] == "ollama" else " Vérifie ta connexion Internet."
            raise LLMError(f"Impossible de joindre {self.name} ({self.base}).{hint}") from e
        except requests.RequestException as e:
            raise LLMError(f"Connexion impossible à {self.name} : {e}") from e
