# DVDExtractor

Application web desktop pour détecter un lecteur DVD et lancer un rip/encodage MP4 en local.

## Ce que fait l'application

- Détection automatique des lecteurs optiques (via `drutil`, puis fallback `diskutil`).
- Détection de chiffrement/encryptage par heuristique.
- Lancement d'extraction/encodage avec priorite au pretraitement natif, puis fallback `ffmpeg`.
- Jobs en temps réel : heartbeat, progression, logs complets, erreurs.
- Téléchargement direct des MP4 depuis l'interface.
- Stockage configurable via `storage` pointant par défaut vers `/Volumes/mac_s1/dvd_mp4`.
- Manifest VOB natif (C++) pour lister les segments sans heuristique fragile.
- Parseur/demux MPEG Program Stream maison (C/C++ + ASM pour primitives memoire) capable d'extraire flux video MPEG-2 et pistes audio AC3/DTS/MPEG depuis un VOB.

## Lancement

```bash
make run
```

Par défaut l'app écoute sur `127.0.0.1:8080`.

## Modes d'extraction

- **normal** (défaut): stratégie ffmpeg standard.
- **engineer**: active plusieurs stratégies automatiquement:
  - lecture directe disque (`/dev/rdiskX` + `/dev/diskX`)
  - navigation `dvd://` quand le build FFmpeg la supporte
  - `-dvd_device` quand disponible
  - fallback format `-f dvd`
  - fallback montage `/VIDEO_TS` (VOB direct + concat)
  - vérification de sortie avec `ffprobe`, puis retries ciblés
  - fallback `HandBrakeCLI` si installé

Le mode ingénieur est configurable via le bouton dans l'UI.

## Structure

- `main.py` : point d'entrée serveur HTTP.
- `dvdapp/` : cœur Python (scanner, jobs, serveur).
- `dvdapp/vob_manifest.py` : wrapper qui appelle le scanner natif.
- `native/` : utilitaires C/C++/ASM structurés proprement (`native/src/*` + `native/include/*`) pour les binaires :
  - `dvd_entropy`
  - `dvd_vob_manifest`
  - `dvd_reader_dump`
  - `dvd_homebrew`
  - `dvd_signal_probe`
- `native/src/homebrew/ps_demux.c` + `program_stream_demuxer.cpp` : demux PES maison utilise par `dvd_homebrew demux` et par le chemin d'extraction natif.
- `static/index.html`, `static/css/app.css`, `static/js/app.js` : UI desktop (Sakura), logs visibles en direct.

## Commandes utiles

- `make run` : compile les sources natives, nettoie les logs ffmpeg temporaires puis lance l'UI.
- `make storage` : affiche le dossier de stockage résolu.
- `make clean` : nettoie les objets/build natifs.
- `native/build/dvd_homebrew demux --input movie.vob --output-dir /tmp/demux` : extrait les flux elementaires sans utiliser ffmpeg pour le demux.
- `python3 main.py --host 127.0.0.1 --port 8080` : même comportement sans Makefile.

## Variables d'environnement

- `DVD_EXTRACT_STORAGE_ROOT` (défaut: `/Volumes/mac_s1`)
- `DVD_EXTRACT_STORAGE_DIRNAME` (défaut: `dvd_mp4`)
- `DVD_EXTRACT_FORCE_LINK` (`1` pour repointer le lien `storage`)
- `DVD_EXTRACT_ALLOW_LOCAL_FALLBACK` (`1` pour autoriser fallback local)
- `DVD_EXTRACT_DEBUG` (`1` pour mode log très verbeux)

## Dépannage

- Si l'application ne démarre pas, vérifiez les droits réseau du port.
- Si rien n'est détecté, vérifiez `drutil`, que le disque est bien inséré et monté.
- En mode ingénieur, les essais sont plus longs (meilleure robustesse).
- Vérifiez la colonne `Erreurs + log` et le panneau `Debug live` pour diagnostiquer les erreurs.

## Ordre de stratégie (mode ingénieur)

1. Lecture directe disque (`/dev/rdiskX`/`/dev/diskX`).
2. Tentatives transcode/copy avec fallback permissif.
3. Protocole `dvd://` si supporté par ffmpeg.
4. `-dvd_device` + format `-f dvd` quand dispo.
5. Montage `VIDEO_TS` direct + concat VOB.
6. Demux natif VOB -> flux elementaires puis encode/mux MP4.
7. Fallback transcode direct VOB via ffmpeg.
8. `HandBrakeCLI` si installé.
