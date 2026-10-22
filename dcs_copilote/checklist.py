"""Mode checklist : une procédure déroulée étape par étape (« suivant » ou appui bref sur le PTT)."""
import re
import unicodedata

MAX_STEPS = 40

_START = re.compile(r"\b(check[- ]?list|checklist)\b"
                    r"|^(lance|lances|demarre|commence|deroule|fais|fait|on fait|on lance)(-moi|-nous| moi| nous)?"
                    r"\s+(la |une |le )?(procedure|sequence)\b")
_COMMANDS = {
    "next": r"suivant|suivante|etape suivante|ok|fait|c'?est fait|next|continue|apres",
    "prev": r"precedent|precedente|etape precedente|retour|reviens|recule",
    "repeat": r"repete|repeter|redis|encore|quoi",
    "stop": r"stop|fin|termine|terminer|arrete|annule|quitte",
}


def _plain(text):
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w' -]", " ", text).strip()


def is_start(question):
    return bool(_START.search(_plain(question)))


def command(text):
    """'next' / 'prev' / 'repeat' / 'stop' si le texte est une commande courte de checklist, sinon None."""
    t = _plain(text)
    t = re.sub(r"\b(la |le )?(checklist|check list|procedure)\b", "", t).strip()
    if not t or len(t.split()) > 4:
        return None
    for cmd, rx in _COMMANDS.items():
        if re.fullmatch(rf"(({rx})\s*)+", t):
            return cmd
    return None


PROMPT = """Tu prépares une checklist que le pilote va dérouler en vol, étape par étape.
- Donne la procédure demandée sous forme de liste numérotée : une seule action par ligne.
- 12 mots maximum par ligne, à l'impératif, dans l'ordre exact de la procédure.
- Mets les noms des commandes du cockpit en anglais entre guillemets « », avec la position voulue.
- Aucune introduction, aucune conclusion, aucun commentaire : seulement les lignes numérotées.
- Si la procédure n'existe pas pour cet appareil, réponds par une seule ligne : 1. Procédure inconnue."""


def parse_steps(text):
    steps = []
    for line in text.splitlines():
        m = re.match(r"\s*(?:\*\*)?\d+\s*[.)\-:]\s*(.+)", line)
        if m:
            step = re.sub(r"\*\*", "", m.group(1)).strip()
            if step:
                steps.append(step)
    return steps[:MAX_STEPS]


class Checklist:
    def __init__(self, title, steps):
        self.title, self.steps, self.i = title, steps, 0

    @property
    def current(self):
        return self.steps[self.i]

    @property
    def done(self):
        return self.i >= len(self.steps)

    def spoken(self, first=False):
        head = f"{self.title}, {len(self.steps)} étapes. " if first else ""
        return f"{head}Étape {self.i + 1} : {self.current}"
