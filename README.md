# CRATE

Local-first web tool for selecting tracks from a personal Discogs vinyl collection while DJing. It stores the collection, DJ annotations, crates and sets in a local SQLite database.

## Run locally

Install from this folder:

```sh
python3 -m pip install .
```

Then run:

```sh
crate
```

For development without installing the command:

```sh
cd app
python3 server.py --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` on the computer. On a phone/tablet connected to the same Wi-Fi, open `http://YOUR-COMPUTER-IP:8000` (for example `http://192.168.1.25:8000`).

On first launch, CRATE creates `app/data/crate.db`. In **Settings**, paste a Discogs Personal Access Token, validate it, then use **Sync collection**. CRATE identifies the token owner automatically and imports only physical Vinyl releases marked by Discogs as 7" or 12". The token remains in the local SQLite database and is never returned by the server API.

Collection sync is deliberately paced to Discogs' authenticated API limit. Large collections can take several minutes because CRATE requests the complete tracklist for each matching release.

The SQLite database stays on the computer that runs the server. Devices on the same Wi-Fi use the same database when they open that computer’s network address.
