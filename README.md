# CRATE

Local-first web tool for selecting 7-inch records while DJing. It imports the supplied collection, stores edits, crates and sets in a local SQLite database, and includes collection search, editable tracks, a current-track view, suggested next records, a path explorer, and set history.

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

On first launch, CRATE creates `app/data/crate.db` and seeds the genre/style enums. Use **Import CSV** in the app to import a private collection; that CSV remains local and is not part of the installed package. The browser keeps a temporary local copy as an offline fallback, but the SQLite database is the source of truth when using `server.py`. Importing a CSV updates an existing record when its Track Number, title and artist match.

The SQLite database stays on the computer that runs the server. Devices on the same Wi-Fi use the same database when they open that computer’s network address.
