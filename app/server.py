"""CRATE local server: serves the web app and persists its data in SQLite."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "crate.db"
WRITE_LOCK = threading.Lock()

TRACK_FIELDS = {
    "Track Number": "track_number", "Track Name": "track_name", "Artist Name(s)": "artist_names",
    "Year": "year", "Popularity": "popularity", "Danceability": "danceability", "Energy": "spotify_energy",
    "Key": "spotify_key", "Loudness": "loudness", "Mode": "spotify_mode", "Speechiness": "speechiness",
    "Acousticness": "acousticness", "Instrumentalness": "instrumentalness", "Liveness": "liveness",
    "Valence": "valence", "Tempo": "tempo", "Time Signature": "time_signature", "Side (A/B)": "side",
    "DJ Energy (1–5)": "dj_energy", "Funkiness (1–5)": "funkiness", "Heaviness (1–5)": "heaviness",
    "Psychedelia (1–5)": "psychedelia", "Groove": "groove", "Mood": "mood", "Vocals": "vocals",
    "Vocal Intensity (0–3)": "vocal_intensity", "Intro": "intro", "Outro": "outro",
    "Mixability (1–5)": "mixability", "Set Role": "set_role", "DJ Rating (1–5)": "dj_rating",
    "DJ Notes": "dj_notes", "Goes Well Into": "goes_well_into",
}
REVERSE_TRACK_FIELDS = {value: key for key, value in TRACK_FIELDS.items()}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tracks (
  id TEXT PRIMARY KEY, track_number TEXT, track_name TEXT NOT NULL, artist_names TEXT, year INTEGER,
  popularity REAL, danceability REAL, spotify_energy REAL, spotify_key INTEGER, loudness REAL,
  spotify_mode INTEGER, speechiness REAL, acousticness REAL, instrumentalness REAL, liveness REAL,
  valence REAL, tempo REAL, time_signature INTEGER, side TEXT, dj_energy INTEGER, funkiness INTEGER,
  heaviness INTEGER, psychedelia INTEGER, groove TEXT, mood TEXT, vocals TEXT, vocal_intensity INTEGER,
  intro TEXT, outro TEXT, mixability INTEGER, set_role TEXT, dj_rating INTEGER, dj_notes TEXT,
  goes_well_into TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT);
CREATE TABLE IF NOT EXISTS styles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS track_genres (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, genre_id));
CREATE TABLE IF NOT EXISTS track_styles (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, style_id INTEGER NOT NULL REFERENCES styles(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, style_id));
CREATE TABLE IF NOT EXISTS track_tags (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, tag_id));
CREATE TABLE IF NOT EXISTS crates (id TEXT PRIMARY KEY, name TEXT NOT NULL, default_tonight INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crate_tracks (crate_id TEXT NOT NULL REFERENCES crates(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(crate_id, track_id));
CREATE TABLE IF NOT EXISTS sets (id TEXT PRIMARY KEY, crate_id TEXT REFERENCES crates(id) ON DELETE SET NULL, name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','finished','cancelled')), started_at INTEGER NOT NULL, ended_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS set_plays (id INTEGER PRIMARY KEY, set_id TEXT NOT NULL REFERENCES sets(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE RESTRICT, position INTEGER NOT NULL, played_at INTEGER, UNIQUE(set_id, position));
CREATE TABLE IF NOT EXISTS set_track_stats (set_id TEXT NOT NULL REFERENCES sets(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, play_count INTEGER NOT NULL DEFAULT 0, first_position INTEGER, last_position INTEGER, PRIMARY KEY(set_id, track_id));
CREATE TABLE IF NOT EXISTS track_stats (track_id TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE, total_plays INTEGER NOT NULL DEFAULT 0, total_sets INTEGER NOT NULL DEFAULT 0, last_played_at INTEGER, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_set_plays_track ON set_plays(track_id);
CREATE INDEX IF NOT EXISTS idx_crate_tracks_track ON crate_tracks(track_id);
"""

GENRE_COLORS = {"Blues":"#427BB8","Brass & Military":"#89965B","Children's":"#F4D35E","Classical":"#E5DCC5","Electronic":"#35C9D0","Folk, World, & Country":"#C88765","Funk / Soul":"#E99A35","Hip-Hop":"#9467D8","Jazz":"#459A92","Latin":"#F07868","Non-Music":"#929AA6","Pop":"#EC79B5","Reggae":"#68B866","Rock":"#D95757","Stage & Screen":"#BCA164"}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def split_values(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def value_or_none(value: object) -> object:
    return None if value in (None, "") else value


def initialise_database() -> None:
    with connect() as db:
        db.executescript(SCHEMA)
        for name, color in GENRE_COLORS.items():
            db.execute("INSERT OR IGNORE INTO genres(name, color) VALUES (?, ?)", (name, color))
        styles_file = APP_DIR / "styles.json"
        if styles_file.exists():
            for name in json.loads(styles_file.read_text(encoding="utf-8")):
                db.execute("INSERT OR IGNORE INTO styles(name) VALUES (?)", (name,))
        if db.execute("SELECT 1 FROM app_meta WHERE key = 'csv_seeded'").fetchone():
            return
        csv_file = APP_DIR / "7inches-DJ.csv"
        if csv_file.exists():
            now = int(time.time() * 1000)
            with csv_file.open(encoding="utf-8-sig", newline="") as source:
                for index, row in enumerate(csv.DictReader(source), start=1):
                    track = dict(row)
                    track["_id"] = f"seed-{index}"
                    write_track(db, track, now)
        db.execute("INSERT INTO app_meta(key, value) VALUES ('csv_seeded', ?)", (str(int(time.time())),))


def write_relation(db: sqlite3.Connection, table: str, entity_table: str, track_id: str, values: list[str]) -> None:
    db.execute(f"DELETE FROM {table} WHERE track_id = ?", (track_id,))
    for position, name in enumerate(values):
        db.execute(f"INSERT OR IGNORE INTO {entity_table}(name) VALUES (?)", (name,))
        entity_id = db.execute(f"SELECT id FROM {entity_table} WHERE name = ?", (name,)).fetchone()["id"]
        db.execute(f"INSERT INTO {table}(track_id, {entity_table[:-1]}_id, position) VALUES (?, ?, ?)", (track_id, entity_id, position))


def write_track(db: sqlite3.Connection, track: dict, now: int) -> None:
    track_id = str(track.get("_id") or f"track-{now}")
    values = {column: value_or_none(track.get(label)) for label, column in TRACK_FIELDS.items()}
    values.update(id=track_id, created_at=now, updated_at=now)
    columns = list(values)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column not in {"id", "created_at"})
    db.execute(f"INSERT INTO tracks ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {updates}", tuple(values[column] for column in columns))
    write_relation(db, "track_genres", "genres", track_id, split_values(track.get("Genres")))
    write_relation(db, "track_styles", "styles", track_id, split_values(track.get("Subgenre / Style")))
    write_relation(db, "track_tags", "tags", track_id, split_values(track.get("Tags")))


def relation_values(db: sqlite3.Connection, table: str, entity: str, track_id: str) -> str:
    rows = db.execute(f"SELECT {entity}.name FROM {table} JOIN {entity} ON {entity}.id = {table}.{entity[:-1]}_id WHERE {table}.track_id = ? ORDER BY {table}.position", (track_id,)).fetchall()
    return "; ".join(row["name"] for row in rows)


def database_state(db: sqlite3.Connection) -> dict:
    tracks = []
    for row in db.execute("SELECT * FROM tracks ORDER BY CAST(track_number AS INTEGER), track_name"):
        track = {"_id": row["id"]}
        for column, label in REVERSE_TRACK_FIELDS.items():
            value = row[column]
            track[label] = "" if value is None else str(value)
        track["Genres"] = relation_values(db, "track_genres", "genres", row["id"])
        track["Subgenre / Style"] = relation_values(db, "track_styles", "styles", row["id"])
        track["Tags"] = relation_values(db, "track_tags", "tags", row["id"])
        tracks.append(track)
    crates = []
    for crate in db.execute("SELECT * FROM crates ORDER BY created_at"):
        track_ids = [row["track_id"] for row in db.execute("SELECT track_id FROM crate_tracks WHERE crate_id = ? ORDER BY position", (crate["id"],))]
        crates.append({"id": crate["id"], "name": crate["name"], "defaultTonight": bool(crate["default_tonight"]), "trackIds": track_ids})
    finished_sets, active_set = [], None
    for item in db.execute("SELECT * FROM sets ORDER BY started_at DESC"):
        track_ids = [row["track_id"] for row in db.execute("SELECT track_id FROM set_plays WHERE set_id = ? ORDER BY position", (item["id"],))]
        payload = {"id": item["id"], "crateId": item["crate_id"], "name": item["name"], "startedAt": item["started_at"], "trackIds": track_ids}
        if item["ended_at"] is not None:
            payload["endedAt"] = item["ended_at"]
        if item["status"] == "active":
            payload["explicit"] = True
            active_set = payload
        else:
            finished_sets.append(payload)
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM app_settings")}
    return {"tracks": tracks, "crates": crates, "sets": finished_sets, "activeSet": active_set, "currentId": settings.get("current_id"), "history": json.loads(settings.get("history", "[]")), "has_state": settings.get("state_saved") == "1"}


def recompute_stats(db: sqlite3.Connection, now: int) -> None:
    db.execute("DELETE FROM set_track_stats")
    db.execute("""INSERT INTO set_track_stats(set_id, track_id, play_count, first_position, last_position)
                  SELECT set_id, track_id, COUNT(*), MIN(position), MAX(position) FROM set_plays GROUP BY set_id, track_id""")
    db.execute("DELETE FROM track_stats")
    db.execute("""INSERT INTO track_stats(track_id, total_plays, total_sets, last_played_at, updated_at)
                  SELECT track_id, COUNT(*), COUNT(DISTINCT set_id), MAX(played_at), ? FROM set_plays GROUP BY track_id""", (now,))


def save_state(state: dict) -> None:
    now = int(time.time() * 1000)
    with WRITE_LOCK, connect() as db:
        with db:
            incoming_tracks = {str(track["_id"]) for track in state.get("tracks", []) if track.get("_id")}
            existing_tracks = {row["id"] for row in db.execute("SELECT id FROM tracks")}
            for removed_id in existing_tracks - incoming_tracks:
                # The current UI allows deleting a record. Remove its historical play
                # rows too so the local database never blocks that operation.
                db.execute("DELETE FROM set_plays WHERE track_id = ?", (removed_id,))
                db.execute("DELETE FROM tracks WHERE id = ?", (removed_id,))
            for track in state.get("tracks", []):
                write_track(db, track, now)

            incoming_crates = {str(crate["id"]) for crate in state.get("crates", []) if crate.get("id")}
            for row in db.execute("SELECT id FROM crates").fetchall():
                if row["id"] not in incoming_crates:
                    db.execute("DELETE FROM crates WHERE id = ?", (row["id"],))
            for crate in state.get("crates", []):
                crate_id = str(crate["id"])
                db.execute("INSERT INTO crates(id, name, default_tonight, created_at, updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, default_tonight=excluded.default_tonight, updated_at=excluded.updated_at", (crate_id, crate.get("name", "Untitled crate"), int(bool(crate.get("defaultTonight"))), now, now))
                db.execute("DELETE FROM crate_tracks WHERE crate_id = ?", (crate_id,))
                for position, track_id in enumerate(crate.get("trackIds", [])):
                    if track_id in incoming_tracks:
                        db.execute("INSERT INTO crate_tracks(crate_id, track_id, position) VALUES (?, ?, ?)", (crate_id, track_id, position))

            all_sets = list(state.get("sets", []))
            if state.get("activeSet"):
                all_sets.append(state["activeSet"])
            incoming_sets = {str(item["id"]) for item in all_sets if item.get("id")}
            for row in db.execute("SELECT id FROM sets").fetchall():
                if row["id"] not in incoming_sets:
                    db.execute("DELETE FROM sets WHERE id = ?", (row["id"],))
            for item in all_sets:
                set_id = str(item["id"])
                status = "active" if item is state.get("activeSet") else "finished"
                db.execute("INSERT INTO sets(id, crate_id, name, status, started_at, ended_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET crate_id=excluded.crate_id, name=excluded.name, status=excluded.status, started_at=excluded.started_at, ended_at=excluded.ended_at, updated_at=excluded.updated_at", (set_id, item.get("crateId"), item.get("name", "Untitled set"), status, item.get("startedAt", now), item.get("endedAt"), now, now))
                db.execute("DELETE FROM set_plays WHERE set_id = ?", (set_id,))
                for position, track_id in enumerate(item.get("trackIds", []), start=1):
                    if track_id in incoming_tracks:
                        db.execute("INSERT INTO set_plays(set_id, track_id, position, played_at) VALUES (?, ?, ?, ?)", (set_id, track_id, position, item.get("startedAt", now)))
            recompute_stats(db, now)
            settings = {"current_id": state.get("currentId") or "", "history": json.dumps(state.get("history", [])), "state_saved": "1"}
            for key, value in settings.items():
                db.execute("INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now))


class CrateHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/api/state":
            with connect() as db:
                self.send_json(database_state(db))
            return
        if urlparse(self.path).path == "/api/health":
            self.send_json({"ok": True, "database": str(DB_PATH)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/state":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            state = json.loads(self.rfile.read(length))
            save_state(state)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"ok": True})

    def log_message(self, format: str, *args) -> None:
        print(f"[CRATE] {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local CRATE server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    args = parser.parse_args()
    initialise_database()
    server = ThreadingHTTPServer((args.host, args.port), CrateHandler)
    print(f"CRATE is ready at http://localhost:{args.port}")
    print(f"SQLite database: {DB_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCRATE server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
