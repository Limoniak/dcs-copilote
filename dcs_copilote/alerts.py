"""Alertes proactives : le copilote parle de lui-même à partir des données de vol (1 paquet/s).

Seuils volontairement prudents et génériques (avions à réaction) : une alerte doit être maintenue
quelques secondes avant d'être annoncée, puis ne se répète pas avant son délai minimum.
"""
import re
import time

M_TO_FT, MS_TO_KT, MS_TO_FPM = 3.28084, 1.94384, 196.85
HELICOPTERS = ("uh1h", "mi8", "ka50", "ah64", "sa342", "mi24", "oh58", "ch47")
SPAWN_GRACE = 15          # s sans alerte après l'apparition dans l'appareil


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def is_helicopter(aircraft):
    n = _norm(aircraft)
    return any(n.startswith(h) for h in HELICOPTERS)


# id : (libellé, aide, maintien en s, délai mini entre deux annonces en s)
RULES = {
    "gear_approach": ("Train rentré en approche", "Sous 1 000 ft sol, en descente, sous 175 kt, train rentré.", 2, 60),
    "sink_rate": ("Taux de chute près du sol", "Sous 500 ft sol, descente de plus de 2 500 ft/min.", 1, 15),
    "gear_speed": ("Train sorti à grande vitesse", "Train sorti au-dessus de 260 kt.", 2, 60),
    "flaps_speed": ("Volets sortis à grande vitesse", "Volets sortis au-dessus de 260 kt.", 2, 60),
    "low_fuel": ("Carburant bas", "Moins de 15 % du plein constaté pendant le vol.", 3, 300),
    "g_load": ("Facteur de charge élevé", "Plus de 8 G ou moins de −2,5 G.", 1, 30),
}


def _num(f, k):
    v = f.get(k)
    return v if isinstance(v, (int, float)) else None


def evaluate(rule, f, ctx):
    """Message d'alerte si la condition de la règle est remplie, sinon None."""
    ias = (_num(f, "ias_ms") or 0) * MS_TO_KT
    agl, vs, gear, flaps = _num(f, "agl_m"), _num(f, "vs_ms"), _num(f, "gear"), _num(f, "flaps")
    on_ground = agl is not None and agl < 4 and ias < 60
    if on_ground:
        return None
    heli = ctx["heli"]
    if rule == "gear_approach" and not heli and None not in (agl, vs, gear):
        if gear < 0.05 and agl * M_TO_FT < 1000 and vs * MS_TO_FPM < -500 and ias < 175:
            return "Train rentré, on est en approche !"
    elif rule == "sink_rate" and None not in (agl, vs):
        if agl * M_TO_FT < 500 and vs * MS_TO_FPM < -2500:
            return "On descend trop vite, on est bas, redresse !"
    elif rule == "gear_speed" and not heli and gear is not None:
        if gear > 0.5 and ias > 260:
            return f"Train sorti à {ias:.0f} nœuds, réduis !"
    elif rule == "flaps_speed" and not heli and flaps is not None:
        if flaps > 0.2 and ias > 260:
            return f"Volets sortis à {ias:.0f} nœuds, réduis !"
    elif rule == "low_fuel":
        fuel = _num(f, "fuel_kg")
        if fuel is not None and ctx["max_fuel"] > 300 and fuel < 0.15 * ctx["max_fuel"]:
            return f"Carburant bas, il nous reste {fuel:,.0f} kilos.".replace(",", " ")
    elif rule == "g_load" and not heli:
        g = _num(f, "g")
        if g is not None and (g > 8 or g < -2.5):
            return f"{g:.1f} G, doucement sur le manche."
    return None


class Alerts:
    def __init__(self, get_cfg, announce):
        self.get_cfg, self.announce = get_cfg, announce
        self.aircraft = None
        self.reset(None)

    def reset(self, aircraft):
        self.aircraft = aircraft
        self.since = time.monotonic()
        self.ctx = {"heli": is_helicopter(aircraft), "max_fuel": 0.0}
        self.held = {r: 0 for r in RULES}
        self.last = {r: 0.0 for r in RULES}

    def feed(self, f):
        """Appelé à chaque paquet DCS (thread réseau)."""
        cfg = self.get_cfg()
        if not cfg.get("enabled", True) or not f:
            return
        if f.get("aircraft") != self.aircraft:
            self.reset(f.get("aircraft"))
        fuel = _num(f, "fuel_kg")
        if fuel is not None:
            self.ctx["max_fuel"] = max(self.ctx["max_fuel"], fuel)
        now = time.monotonic()
        if now - self.since < SPAWN_GRACE:
            return
        enabled = cfg.get("rules", {})
        for rule, (_label, _hint, hold, cooldown) in RULES.items():
            if not enabled.get(rule, True):
                continue
            msg = evaluate(rule, f, self.ctx)
            self.held[rule] = self.held[rule] + 1 if msg else 0
            if msg and self.held[rule] >= hold and now - self.last[rule] >= cooldown:
                self.last[rule] = now
                self.announce(msg)
