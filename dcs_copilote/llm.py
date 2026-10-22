"""Client minimal pour API compatible OpenAI (/chat/completions en streaming)."""
import json
import os
import time

import requests


class LLMError(Exception):
    pass


NAMES = {"ollama": "Ollama", "openai": "OpenAI", "gemini": "Gemini", "grok": "Grok", "openrouter": "OpenRouter"}
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

        Surcharge passagère (429/5xx) : nouvel essai, puis modèle de secours s'il est configuré.
        on_retry(message) informe l'utilisateur ; alive() faux = question annulée, on abandonne."""
        on_retry = on_retry or (lambda m: None)
        alive = alive or (lambda: True)
        headers = self._headers()
        models = [self.cfg["model"]]
        fallback = (self.cfg.get("fallback_model") or "").strip()
        if fallback and fallback != models[0]:
            models.append(fallback)

        err = None
        for i, model in enumerate(models):
            if i:
                on_retry(f"{models[i - 1]} indisponible → modèle de secours {model}")
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
                if status not in RETRY_STATUS:
                    raise LLMError(err)
                if attempt == len(RETRY_DELAYS):
                    break
                delay = RETRY_DELAYS[attempt]
                try:
                    delay = min(max(delay, float(r.headers.get("Retry-After", 0))), 8)
                except ValueError:
                    pass
                on_retry(f"{self.name} surchargé, nouvel essai dans {delay:.0f} s…")
                end = time.monotonic() + delay
                while time.monotonic() < end and alive():
                    time.sleep(0.1)
        if len(models) == 1:
            err += "\nAstuce : ajoute un modèle de secours dans ⚙ Réglages → IA."
        raise LLMError(err)

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
