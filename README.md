# CRATE

Local-first web tool for selecting 7-inch records while DJing. It imports the supplied collection, saves edits in the browser on the device, and includes collection search, editable tracks, crates, a current-track view, suggested next records, a path explorer, and set history.

## Run locally

From this folder:

```sh
cd app
python3 -m http.server 8000 --bind 0.0.0.0
```

Open `http://localhost:8000` on the computer. On a phone/tablet connected to the same Wi-Fi, open `http://YOUR-COMPUTER-IP:8000` (for example `http://192.168.1.25:8000`).

Your changes are stored in that browser's local storage. Use **Export backup** to download a portable backup before switching devices or clearing browser data. Importing a CSV updates an existing record when its Track Number, title, and artist match.
