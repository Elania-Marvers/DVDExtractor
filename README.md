# DVDExtractor

Petit extracteur DVD avec interface web (Python + C/C++/ASM) pour le projet Mac.

## Ce que fait l'application

- Détection des lecteurs via `drutil` puis fallback disque.
- Détection simple du chiffrement (méthode `lsdvd` si dispo, sinon score d'entropie via utilitaire natif).
- Lancement d'une conversion en MP4 avec `ffmpeg`.
- Listing des fichiers MP4 générés et téléchargement direct depuis le navigateur.
- Stockage configurable, avec symlink automatique de `storage` vers `/Volumes/mac_s1/dvd_mp4`.

## Lancement

```bash
make run
```

Par défaut l'app écoute sur `127.0.0.1:8080`.

## Structure

- `main.py` : point d'entrée serveur HTTP.
- `dvdapp/` : coeur Python.
- `native/` : utilitaire C++/C/ASM utilisé pour la détection d'entropie.

## Commandes utiles

- `make run` compile les sources natives puis lance l'UI.
- `make storage` affiche le dossier de stockage résolu.
- `make clean` nettoie les objets natifs.
- `make storage` affiche le dossier de stockage effectif.

## Variables d'environnement

- `DVD_EXTRACT_STORAGE_ROOT` (défaut: `/Volumes/mac_s1`)
- `DVD_EXTRACT_STORAGE_DIRNAME` (défaut: `dvd_mp4`)
- `DVD_EXTRACT_FORCE_LINK` (`1` pour forcer le repointement du lien `storage`)
- `DVD_EXTRACT_ALLOW_LOCAL_FALLBACK` (`1` pour autoriser le fallback local)

## Avertissement

Cette base vise l'usage personnel sur vos médias personnels. Vérifie la légalité locale avant de dupliquer un disque.
