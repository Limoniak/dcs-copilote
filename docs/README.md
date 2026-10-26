# Site de présentation — GitHub Pages

Site de présentation en anglais, statique, sans compilation ni suivi de visiteurs.
Les polices, images et scripts sont hébergés avec le site : aucun appel à une API ou un CDN au chargement.

La page comprend une galerie de quatre captures agrandissables, un menu mobile,
les téléchargements, la copie des commandes Linux et une FAQ. La navigation au clavier
et la préférence système de réduction des animations sont prises en compte.

Le site est en anglais ; l'interface de l'application reste en français. Cette distinction
est indiquée dans la galerie et la FAQ.

## Prévisualiser

À la racine du dépôt :

```sh
python -m http.server 8000 --directory docs
```

Ouvrir <http://localhost:8000>. Arrêter avec Ctrl+C.

## Publier

1. Dans les paramètres GitHub du dépôt, ouvrir **Settings → Pages**.
2. Sélectionner **GitHub Actions** comme source de déploiement.
3. Pousser ces fichiers sur `main`. Le workflow **GitHub Pages** publie uniquement le dossier `docs/`.
   Si les fichiers sont déjà sur `main`, lancer le workflow depuis **Actions → GitHub Pages → Run workflow**.

Adresse attendue pour ce dépôt : <https://limoniak.github.io/dcs-copilote/>.
L'adresse effective figure dans le résultat du déploiement GitHub Actions.

Documentation : [workflows personnalisés GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Modifier

- `index.html` : textes, sections et liens vers les téléchargements.
- `style.css` : mise en page responsive et couleurs.
- `demo.js` : galerie, agrandissement des captures, menu mobile et copie des commandes.
- `assets/` : logos, illustration, captures de l'application et polices locales.
- [`assets/README.md`](assets/README.md) : provenance des visuels et prompt de génération.

## Refaire les captures

Les images de la galerie sont rendues depuis les vrais composants Qt de l'application,
avec une configuration vierge et du contenu d'exemple. Elles ne représentent pas une session
DCS en cours. Aucune clé API, aucun fichier de configuration personnel et aucun micro ne sont utilisés.

Depuis la racine du dépôt, dans un environnement Python disposant de `PySide6-Essentials`,
`numpy`, `requests` et `sounddevice` :

```sh
python build/capture_site.py
```

Le script remplace les quatre images dans `docs/assets/screenshots/`.
Sous Windows, il charge explicitement Segoe UI pour le rendu Qt hors écran.

## Vérifications

Contrôles dans le navigateur aux largeurs 320, 390, 768, 1024 et 1440 px :
absence de débordement horizontal, menu mobile, galerie, agrandissement et navigation au clavier.
La FAQ et la copie des commandes sont également vérifiées. Ces contrôles ne constituent
pas une validation sur de vrais téléphones ni dans tous les navigateurs.

Les liens relatifs permettent de servir le site sous `/dcs-copilote/` et en local.
Le bouton de téléchargement ouvre la dernière release, sans figer un numéro de version.
