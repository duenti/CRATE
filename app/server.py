"""CRATE local server: serves the web app and persists its data in SQLite."""

from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "crate.db"
WRITE_LOCK = threading.Lock()
DISCOGS_SYNC_LOCK = threading.Lock()
IMPORT_PROGRESS_LOCK = threading.Lock()
IMPORT_PROGRESS: dict[str, object] = {"state": "idle", "completed": 0, "total": 0, "error": None}
DISCOGS_API_ROOT = "https://api.discogs.com"
DISCOGS_USER_AGENT = "CRATE/0.2 (local Discogs collection manager)"

TRACK_FIELDS = {
    "Track Number": "track_number", "Track Name": "track_name", "Artist Name(s)": "artist_names",
    "Year": "year", "Popularity": "popularity", "Danceability": "danceability", "Energy": "spotify_energy",
    "Key": "spotify_key", "Loudness": "loudness", "Mode": "spotify_mode", "Speechiness": "speechiness",
    "Acousticness": "acousticness", "Instrumentalness": "instrumentalness", "Liveness": "liveness",
    "Valence": "valence", "Tempo": "tempo", "Time Signature": "time_signature", "Side (A/B)": "side",
    "DJ Energy (1–5)": "dj_energy", "Funkiness (1–5)": "funkiness", "Heaviness (1–5)": "heaviness",
    "Psychedelia (1–5)": "psychedelia", "Vocal Intensity (0–3)": "vocal_intensity",
    "Mixability (1–5)": "mixability", "DJ Rating (1–5)": "dj_rating",
    "DJ Notes": "dj_notes", "Goes Well Into": "goes_well_into",
}
REVERSE_TRACK_FIELDS = {value: key for key, value in TRACK_FIELDS.items()}

SCHEMA_VERSION = "2"

# `records` represents an owned physical item (a Discogs collection instance),
# while `tracks` is the release-specific track list used in crates and sets.
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY, discogs_instance_id INTEGER NOT NULL UNIQUE, discogs_release_id INTEGER NOT NULL,
  discogs_master_id INTEGER, title TEXT NOT NULL, year INTEGER, country TEXT, resource_url TEXT,
  discogs_uri TEXT, cover_image_url TEXT, thumb_image_url TEXT, folder_id INTEGER, folder_name TEXT,
  rating INTEGER CHECK(rating BETWEEN 0 AND 5), media_condition TEXT, sleeve_condition TEXT,
  collection_notes TEXT, added_at INTEGER, is_in_collection INTEGER NOT NULL DEFAULT 1 CHECK(is_in_collection IN (0, 1)),
  last_synced_at INTEGER NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS record_formats (
  id INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  position INTEGER NOT NULL, name TEXT NOT NULL, quantity INTEGER, text TEXT,
  descriptions_json TEXT NOT NULL DEFAULT '[]', UNIQUE(record_id, position)
);
CREATE TABLE IF NOT EXISTS artists (id INTEGER PRIMARY KEY, discogs_artist_id INTEGER UNIQUE, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS record_artists (
  record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE, artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL, name_variation TEXT, join_phrase TEXT, PRIMARY KEY(record_id, artist_id, position)
);
CREATE TABLE IF NOT EXISTS labels (id INTEGER PRIMARY KEY, discogs_label_id INTEGER UNIQUE, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS record_labels (
  record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE, label_id INTEGER NOT NULL REFERENCES labels(id) ON DELETE RESTRICT,
  catalog_number TEXT, position INTEGER NOT NULL, PRIMARY KEY(record_id, label_id, position)
);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT);
CREATE TABLE IF NOT EXISTS styles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS record_genres (record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE, genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(record_id, genre_id));
CREATE TABLE IF NOT EXISTS record_styles (record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE, style_id INTEGER NOT NULL REFERENCES styles(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(record_id, style_id));
CREATE TABLE IF NOT EXISTS tracks (
  id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  discogs_position TEXT NOT NULL, position_index INTEGER NOT NULL, title TEXT NOT NULL,
  duration_text TEXT, duration_seconds INTEGER, track_type TEXT NOT NULL DEFAULT 'track',
  is_audio INTEGER NOT NULL DEFAULT 1 CHECK(is_audio IN (0, 1)),
  popularity REAL, danceability REAL, spotify_energy REAL, spotify_key INTEGER, loudness REAL,
  spotify_mode INTEGER, speechiness REAL, acousticness REAL, instrumentalness REAL, liveness REAL,
  valence REAL, tempo REAL, time_signature INTEGER, dj_energy INTEGER, funkiness INTEGER,
  heaviness INTEGER, psychedelia INTEGER, vocal_intensity INTEGER, mixability INTEGER, dj_rating INTEGER,
  dj_notes TEXT, goes_well_into TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  UNIQUE(record_id, discogs_position)
);
CREATE TABLE IF NOT EXISTS track_artists (
  track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, artist_id INTEGER NOT NULL REFERENCES artists(id) ON DELETE RESTRICT,
  position INTEGER NOT NULL, name_variation TEXT, join_phrase TEXT, PRIMARY KEY(track_id, artist_id, position)
);
CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS track_genres (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, genre_id INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, genre_id));
CREATE TABLE IF NOT EXISTS track_styles (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, style_id INTEGER NOT NULL REFERENCES styles(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, style_id));
CREATE TABLE IF NOT EXISTS track_tags (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, tag_id));
CREATE TABLE IF NOT EXISTS moods (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS grooves (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS vocals (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS set_roles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS intros (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS outros (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS track_moods (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, mood_id INTEGER NOT NULL REFERENCES moods(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, mood_id));
CREATE TABLE IF NOT EXISTS track_grooves (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, groove_id INTEGER NOT NULL REFERENCES grooves(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, groove_id));
CREATE TABLE IF NOT EXISTS track_vocals (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, vocal_id INTEGER NOT NULL REFERENCES vocals(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, vocal_id));
CREATE TABLE IF NOT EXISTS track_set_roles (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, set_role_id INTEGER NOT NULL REFERENCES set_roles(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, set_role_id));
CREATE TABLE IF NOT EXISTS track_intros (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, intro_id INTEGER NOT NULL REFERENCES intros(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, intro_id));
CREATE TABLE IF NOT EXISTS track_outros (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, outro_id INTEGER NOT NULL REFERENCES outros(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(track_id, outro_id));
CREATE TABLE IF NOT EXISTS crates (id TEXT PRIMARY KEY, name TEXT NOT NULL, default_tonight INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS crate_tracks (crate_id TEXT NOT NULL REFERENCES crates(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, position INTEGER NOT NULL, PRIMARY KEY(crate_id, track_id));
CREATE TABLE IF NOT EXISTS sets (id TEXT PRIMARY KEY, crate_id TEXT REFERENCES crates(id) ON DELETE SET NULL, name TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('active','finished','cancelled')), started_at INTEGER NOT NULL, ended_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS set_plays (id INTEGER PRIMARY KEY, set_id TEXT NOT NULL REFERENCES sets(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE RESTRICT, position INTEGER NOT NULL, played_at INTEGER, UNIQUE(set_id, position));
CREATE TABLE IF NOT EXISTS set_track_stats (set_id TEXT NOT NULL REFERENCES sets(id) ON DELETE CASCADE, track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE, play_count INTEGER NOT NULL DEFAULT 0, first_position INTEGER, last_position INTEGER, PRIMARY KEY(set_id, track_id));
CREATE TABLE IF NOT EXISTS track_stats (track_id TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE, total_plays INTEGER NOT NULL DEFAULT 0, total_sets INTEGER NOT NULL DEFAULT 0, last_played_at INTEGER, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS discogs_sync_state (
  id INTEGER PRIMARY KEY CHECK(id = 1), username TEXT, last_started_at INTEGER, last_completed_at INTEGER,
  last_error TEXT, continuation_page INTEGER, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_set_plays_track ON set_plays(track_id);
CREATE INDEX IF NOT EXISTS idx_crate_tracks_track ON crate_tracks(track_id);
CREATE INDEX IF NOT EXISTS idx_tracks_record_position ON tracks(record_id, position_index);
CREATE INDEX IF NOT EXISTS idx_records_release ON records(discogs_release_id);
"""

GENRE_COLORS = {"Blues":"#427BB8","Brass & Military":"#89965B","Children's":"#F4D35E","Classical":"#E5DCC5","Electronic":"#35C9D0","Folk, World, & Country":"#C88765","Funk / Soul":"#E99A35","Hip-Hop":"#9467D8","Jazz":"#459A92","Latin":"#F07868","Non-Music":"#929AA6","Pop":"#EC79B5","Reggae":"#68B866","Rock":"#D95757","Stage & Screen":"#BCA164"}

ENUM_VALUES = {
    "moods": ["Upbeat", "Warm", "Sunny", "Playful", "Celebratory", "Uplifting", "Euphoric", "Sultry", "Smooth", "Laid-back", "Hypnotic", "Dreamy", "Spacious", "Reflective", "Moody", "Dark", "Gritty", "Urgent", "Defiant", "Dramatic", "Futuristic", "Cool", "Rowdy"],
    "grooves": ["Straight", "Syncopated", "Four-on-the-floor", "Shuffle", "Swing", "Offbeat / Skank", "Breakbeat", "Latin / Clave", "Polyrhythmic", "Loose", "Driving", "Half-time", "Free / Rubato"],
    "vocals": ["Instrumental", "Mostly instrumental", "Sung", "Spoken", "Rap", "Chants", "Vocoder", "Wordless vocals"],
    "set_roles": ["Opener", "Warm-up", "Builder", "Peak", "Bridge", "Reset", "Closer"],
    "intros": ["Drums / Percussion", "Bass-led", "Guitar-led", "Keys / Synth-led", "Horns-led", "Full-band", "Vocal-first", "Spoken", "Atmospheric", "Gradual build", "Cold start"],
    "outros": ["Fade-out", "Cold ending", "Instrumental tail", "Vocal ending", "Break / Stop", "Gradual breakdown"],
}

ENUM_ALIASES = {
    "moods": {"Bright":"Sunny", "Cheeky":"Playful", "Driving":"Urgent", "Earthy":"Warm", "Edgy":"Gritty", "Empowering":"Uplifting", "Explosive":"Euphoric", "Exuberant":"Celebratory", "Friendly":"Warm", "Party":"Celebratory", "Punchy":"Urgent", "Slinky":"Smooth", "Soulful":"Warm", "Streetwise":"Cool", "Triumphant":"Celebratory"},
    "grooves": {"Four-on-the-floor / syncopated":"Four-on-the-floor; Syncopated", "Loose / syncopated":"Loose; Syncopated", "Offbeat / skank":"Offbeat / Skank", "Percussive / syncopated":"Syncopated", "Straight rock / backbeat":"Straight", "Syncopated funk":"Syncopated"},
    "vocals": {"Spoken / sung":"Spoken; Sung", "Vocal":"Sung"},
    "set_roles": {"Warm-up / Builder":"Warm-up; Builder", "Warm-up / Reset":"Warm-up; Reset"},
    "intros": {"Atmospheric instrumental textures (verify edit)":"Atmospheric", "Bass-led instrumental groove (verify edit)":"Bass-led", "Dramatic instrumental theme / build (verify edit)":"Gradual build", "Guitar riff / vocal chant (verify edit)":"Guitar-led", "Guitar-led instrumental riff (verify edit)":"Guitar-led", "High guitar motif / instrumental groove (verify edit)":"Guitar-led", "Immediate vocal entrance (verify edit)":"Vocal-first", "Instrumental psychedelic motif (verify edit)":"Atmospheric", "Orchestral Beethoven motif (verify edit)":"Full-band", "Organ-led riff (verify edit)":"Keys / Synth-led", "Percussion-led instrumental groove (verify edit)":"Drums / Percussion", "Synth sequencer / electronic beat (verify edit)":"Keys / Synth-led"},
}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def split_values(value: object) -> list[str]:
    return [part.strip() for part in str(value or "").split(";") if part.strip()]


def split_enum_values(value: object, enum_table: str) -> list[str]:
    """Split semicolon values and legacy slash-separated values safely."""
    known = {name.casefold(): name for name in ENUM_VALUES.get(enum_table, [])}
    result = []
    aliases = ENUM_ALIASES.get(enum_table, {})
    for part in split_values(value):
        alias = aliases.get(part) or aliases.get(part.title())
        if alias:
            result.extend(split_values(alias))
            continue
        if part.casefold() in known:
            result.append(known[part.casefold()])
            continue
        pieces = [piece.strip() for piece in part.split("/") if piece.strip()]
        result.extend(known.get(piece.casefold(), piece) for piece in pieces)
    return result


def allowed_values(entity_table: str) -> set[str] | None:
    if entity_table in ENUM_VALUES:
        return set(ENUM_VALUES[entity_table])
    if entity_table == "genres":
        return set(GENRE_COLORS)
    if entity_table == "styles":
        styles_file = APP_DIR / "styles.json"
        if styles_file.exists():
            return set(json.loads(styles_file.read_text(encoding="utf-8")))
    return None


def value_or_none(value: object) -> object:
    return None if value in (None, "") else value


def initialise_database() -> None:
    with connect() as db:
        # The v2 migration deliberately retains only app settings. The user has
        # backed up the old database and explicitly chose a clean Discogs import.
        db.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
        version = db.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
        if version is None or version["value"] != SCHEMA_VERSION:
            db.executescript("""
                DROP TABLE IF EXISTS set_track_stats;
                DROP TABLE IF EXISTS track_stats;
                DROP TABLE IF EXISTS set_plays;
                DROP TABLE IF EXISTS crate_tracks;
                DROP TABLE IF EXISTS sets;
                DROP TABLE IF EXISTS crates;
                DROP TABLE IF EXISTS track_genres;
                DROP TABLE IF EXISTS track_styles;
                DROP TABLE IF EXISTS track_tags;
                DROP TABLE IF EXISTS track_moods;
                DROP TABLE IF EXISTS track_grooves;
                DROP TABLE IF EXISTS track_vocals;
                DROP TABLE IF EXISTS track_set_roles;
                DROP TABLE IF EXISTS track_intros;
                DROP TABLE IF EXISTS track_outros;
                DROP TABLE IF EXISTS track_artists;
                DROP TABLE IF EXISTS tracks;
                DROP TABLE IF EXISTS record_genres;
                DROP TABLE IF EXISTS record_styles;
                DROP TABLE IF EXISTS record_artists;
                DROP TABLE IF EXISTS record_labels;
                DROP TABLE IF EXISTS record_formats;
                DROP TABLE IF EXISTS records;
                DROP TABLE IF EXISTS artists;
                DROP TABLE IF EXISTS labels;
                DROP TABLE IF EXISTS genres;
                DROP TABLE IF EXISTS styles;
                DROP TABLE IF EXISTS tags;
                DROP TABLE IF EXISTS moods;
                DROP TABLE IF EXISTS grooves;
                DROP TABLE IF EXISTS vocals;
                DROP TABLE IF EXISTS set_roles;
                DROP TABLE IF EXISTS intros;
                DROP TABLE IF EXISTS outros;
                DROP TABLE IF EXISTS discogs_sync_state;
                DROP TABLE IF EXISTS app_meta;
            """)
        db.executescript(SCHEMA)
        for table, values in ENUM_VALUES.items():
            for name in values:
                db.execute(f"INSERT OR IGNORE INTO {table}(name) VALUES (?)", (name,))
        now = int(time.time() * 1000)
        db.execute(
            "INSERT INTO app_settings(key, value, updated_at) VALUES ('schema_version', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (SCHEMA_VERSION, now),
        )


def write_relation(db: sqlite3.Connection, table: str, entity_table: str, track_id: str, values: list[str]) -> None:
    db.execute(f"DELETE FROM {table} WHERE track_id = ?", (track_id,))
    allowed = allowed_values(entity_table)
    for position, name in enumerate(values):
        if allowed is not None and name not in allowed:
            continue
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
    for label, relation_table, entity_table in [("Mood", "track_moods", "moods"), ("Groove", "track_grooves", "grooves"), ("Vocals", "track_vocals", "vocals"), ("Set Role", "track_set_roles", "set_roles"), ("Intro", "track_intros", "intros"), ("Outro", "track_outros", "outros")]:
        write_relation(db, relation_table, entity_table, track_id, split_enum_values(track.get(label), entity_table))


def relation_values(db: sqlite3.Connection, table: str, entity: str, track_id: str) -> str:
    rows = db.execute(f"SELECT {entity}.name FROM {table} JOIN {entity} ON {entity}.id = {table}.{entity[:-1]}_id WHERE {table}.track_id = ? ORDER BY {table}.position", (track_id,)).fetchall()
    return "; ".join(row["name"] for row in rows)


def database_state(db: sqlite3.Connection) -> dict:
    settings = {row["key"]: row["value"] for row in db.execute("SELECT key, value FROM app_settings WHERE key != 'discogs_token'")}
    records = []
    for row in db.execute("SELECT * FROM records WHERE is_in_collection = 1 ORDER BY added_at DESC, title"):
        artists = [artist["name"] for artist in db.execute("SELECT a.name FROM record_artists ra JOIN artists a ON a.id = ra.artist_id WHERE ra.record_id = ? ORDER BY ra.position", (row["id"],))]
        formats = [format_row["name"] + (f" · {format_row['text']}" if format_row["text"] else "") + (f" · {', '.join(json.loads(format_row['descriptions_json']))}" if format_row["descriptions_json"] else "") for format_row in db.execute("SELECT * FROM record_formats WHERE record_id = ? ORDER BY position", (row["id"],))]
        genres = [item["name"] for item in db.execute("SELECT g.name FROM record_genres rg JOIN genres g ON g.id = rg.genre_id WHERE rg.record_id = ? ORDER BY rg.position", (row["id"],))]
        styles = [item["name"] for item in db.execute("SELECT s.name FROM record_styles rs JOIN styles s ON s.id = rs.style_id WHERE rs.record_id = ? ORDER BY rs.position", (row["id"],))]
        tracks = [{"position": track["discogs_position"], "title": track["title"], "duration": track["duration_text"]} for track in db.execute("SELECT discogs_position, title, duration_text FROM tracks WHERE record_id = ? ORDER BY position_index", (row["id"],))]
        records.append({"id": row["id"], "title": row["title"], "artists": ", ".join(artists), "year": row["year"], "country": row["country"], "formats": formats, "genres": genres, "styles": styles, "tracks": tracks, "coverImage": row["cover_image_url"], "addedAt": row["added_at"]})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "records": records,
        "tracks": [],
        "settings": settings,
        # Compatibility placeholders until the browser UI is updated in step 3.
        "crates": [], "sets": [], "activeSet": None, "currentId": None,
        "history": [], "matchingSettings": {}, "has_state": True,
        "discogsTokenConfigured": bool(db.execute("SELECT 1 FROM app_settings WHERE key = 'discogs_token' AND value != ''").fetchone()),
    }


class DiscogsAPIError(RuntimeError):
    """A safe, user-facing failure returned by the Discogs API."""


class DiscogsClient:
    """Small dependency-free Discogs client with conservative rate limiting."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.last_request_at = 0.0

    def get(self, path: str, **params: object) -> dict:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{DISCOGS_API_ROOT}{path}" + (f"?{query}" if query else "")
        for attempt in range(3):
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < 1.05:
                time.sleep(1.05 - elapsed)
            request = Request(url, headers={
                "Authorization": f"Discogs token={self.token}",
                "User-Agent": DISCOGS_USER_AGENT,
                "Accept": "application/vnd.discogs.v2.discogs+json",
            })
            try:
                with urlopen(request, timeout=30) as response:
                    self.last_request_at = time.monotonic()
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                self.last_request_at = time.monotonic()
                if error.code == HTTPStatus.TOO_MANY_REQUESTS and attempt < 2:
                    retry_after = error.headers.get("Retry-After", "60")
                    try:
                        time.sleep(max(1, min(int(retry_after), 120)))
                    except ValueError:
                        time.sleep(60)
                    continue
                if error.code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise DiscogsAPIError("Discogs rejected this token. Check it in Settings.") from error
                raise DiscogsAPIError(f"Discogs returned HTTP {error.code}.") from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                raise DiscogsAPIError("Could not reach Discogs. Check your connection and try again.") from error
        raise DiscogsAPIError("Discogs rate limit was reached. Try again shortly.")


def discogs_token() -> str:
    with connect() as db:
        row = db.execute("SELECT value FROM app_settings WHERE key = 'discogs_token'").fetchone()
    if not row or not row["value"].strip():
        raise DiscogsAPIError("Add your Discogs Personal Access Token in Settings first.")
    return row["value"].strip()


def parse_discogs_date(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def duration_seconds(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parts = [int(part) for part in value.split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def is_dj_vinyl(release: dict) -> bool:
    """Keep vinyl records explicitly described by Discogs as 7-inch or 12-inch."""
    for format_data in release.get("formats", []):
        if str(format_data.get("name", "")).casefold() != "vinyl":
            continue
        details = [str(format_data.get("text", "")), *map(str, format_data.get("descriptions", []))]
        if any(size in detail for detail in details for size in ('7"', '12"')):
            return True
    return False


def upsert_artist(db: sqlite3.Connection, artist: dict) -> int:
    discogs_id = artist.get("id")
    name = str(artist.get("name") or "Unknown artist")
    if isinstance(discogs_id, int):
        db.execute("INSERT INTO artists(discogs_artist_id, name) VALUES (?, ?) ON CONFLICT(discogs_artist_id) DO UPDATE SET name=excluded.name", (discogs_id, name))
        return db.execute("SELECT id FROM artists WHERE discogs_artist_id = ?", (discogs_id,)).fetchone()["id"]
    db.execute("INSERT OR IGNORE INTO artists(name) VALUES (?)", (name,))
    return db.execute("SELECT id FROM artists WHERE name = ?", (name,)).fetchone()["id"]


def replace_artists(db: sqlite3.Connection, table: str, owner_column: str, owner_id: str, artists: list[dict]) -> None:
    db.execute(f"DELETE FROM {table} WHERE {owner_column} = ?", (owner_id,))
    for position, artist in enumerate(artists):
        artist_id = upsert_artist(db, artist)
        db.execute(
            f"INSERT INTO {table}({owner_column}, artist_id, position, name_variation, join_phrase) VALUES (?, ?, ?, ?, ?)",
            (owner_id, artist_id, position, artist.get("anv"), artist.get("join")),
        )


def replace_record_names(db: sqlite3.Connection, relation_table: str, entity_table: str, record_id: str, names: list[object]) -> None:
    entity_id = f"{entity_table[:-1]}_id"
    db.execute(f"DELETE FROM {relation_table} WHERE record_id = ?", (record_id,))
    for position, name in enumerate(dict.fromkeys(str(name).strip() for name in names if str(name).strip())):
        db.execute(f"INSERT OR IGNORE INTO {entity_table}(name) VALUES (?)", (name,))
        row = db.execute(f"SELECT id FROM {entity_table} WHERE name = ?", (name,)).fetchone()
        db.execute(f"INSERT INTO {relation_table}(record_id, {entity_id}, position) VALUES (?, ?, ?)", (record_id, row["id"], position))


def import_discogs_record(db: sqlite3.Connection, collection_item: dict, release: dict, synced_at: int) -> int:
    basic = collection_item.get("basic_information") or {}
    instance_id = collection_item.get("instance_id")
    release_id = release.get("id") or basic.get("id")
    if not isinstance(instance_id, int) or not isinstance(release_id, int):
        raise DiscogsAPIError("Discogs returned a collection item without a release identity.")
    record_id = f"discogs-instance-{instance_id}"
    now = int(time.time() * 1000)
    images = release.get("images") or basic.get("images") or []
    primary_image = next((image for image in images if image.get("type") == "primary"), images[0] if images else {})
    db.execute(
        """INSERT INTO records(id, discogs_instance_id, discogs_release_id, discogs_master_id, title, year, country, resource_url, discogs_uri, cover_image_url, thumb_image_url, folder_id, folder_name, rating, media_condition, sleeve_condition, collection_notes, added_at, is_in_collection, last_synced_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET discogs_release_id=excluded.discogs_release_id, discogs_master_id=excluded.discogs_master_id, title=excluded.title, year=excluded.year, country=excluded.country, resource_url=excluded.resource_url, discogs_uri=excluded.discogs_uri, cover_image_url=excluded.cover_image_url, thumb_image_url=excluded.thumb_image_url, folder_id=excluded.folder_id, rating=excluded.rating, collection_notes=excluded.collection_notes, added_at=excluded.added_at, is_in_collection=1, last_synced_at=excluded.last_synced_at, updated_at=excluded.updated_at""",
        (record_id, instance_id, release_id, release.get("master_id") or basic.get("master_id"), release.get("title") or basic.get("title") or "Untitled", release.get("year") or basic.get("year"), release.get("country"), release.get("resource_url") or basic.get("resource_url"), release.get("uri") or basic.get("uri"), primary_image.get("uri"), primary_image.get("uri150") or basic.get("thumb"), collection_item.get("folder_id"), None, collection_item.get("rating"), collection_item.get("media_condition"), collection_item.get("sleeve_condition"), collection_item.get("notes"), parse_discogs_date(collection_item.get("date_added")), synced_at, now, now),
    )
    db.execute("DELETE FROM record_formats WHERE record_id = ?", (record_id,))
    for position, format_data in enumerate(release.get("formats") or basic.get("formats") or []):
        db.execute("INSERT INTO record_formats(record_id, position, name, quantity, text, descriptions_json) VALUES (?, ?, ?, ?, ?, ?)", (record_id, position, format_data.get("name", "Unknown"), format_data.get("qty"), format_data.get("text"), json.dumps(format_data.get("descriptions", []))))
    replace_artists(db, "record_artists", "record_id", record_id, release.get("artists") or basic.get("artists") or [])
    db.execute("DELETE FROM record_labels WHERE record_id = ?", (record_id,))
    for position, label in enumerate(release.get("labels") or basic.get("labels") or []):
        label_id = label.get("id")
        name = str(label.get("name") or "Unknown label")
        if isinstance(label_id, int):
            db.execute("INSERT INTO labels(discogs_label_id, name) VALUES (?, ?) ON CONFLICT(discogs_label_id) DO UPDATE SET name=excluded.name", (label_id, name))
            local_label_id = db.execute("SELECT id FROM labels WHERE discogs_label_id = ?", (label_id,)).fetchone()["id"]
        else:
            db.execute("INSERT OR IGNORE INTO labels(name) VALUES (?)", (name,))
            local_label_id = db.execute("SELECT id FROM labels WHERE name = ?", (name,)).fetchone()["id"]
        db.execute("INSERT INTO record_labels(record_id, label_id, catalog_number, position) VALUES (?, ?, ?, ?)", (record_id, local_label_id, label.get("catno"), position))
    replace_record_names(db, "record_genres", "genres", record_id, release.get("genres") or basic.get("genres") or [])
    replace_record_names(db, "record_styles", "styles", record_id, release.get("styles") or basic.get("styles") or [])
    imported_tracks = 0
    for source_index, track in enumerate(release.get("tracklist") or []):
        if track.get("type_", "track") != "track":
            continue
        imported_tracks += 1
        track_id = f"{record_id}:track-{source_index}"
        position = str(track.get("position") or source_index + 1)
        db.execute(
            """INSERT INTO tracks(id, record_id, discogs_position, position_index, title, duration_text, duration_seconds, track_type, is_audio, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET discogs_position=excluded.discogs_position, position_index=excluded.position_index, title=excluded.title, duration_text=excluded.duration_text, duration_seconds=excluded.duration_seconds, track_type=excluded.track_type, updated_at=excluded.updated_at""",
            (track_id, record_id, position, source_index, track.get("title") or "Untitled", track.get("duration"), duration_seconds(track.get("duration")), track.get("type_", "track"), now, now),
        )
        replace_artists(db, "track_artists", "track_id", track_id, track.get("artists") or [])
    return imported_tracks


def update_sync_state(**values: object) -> None:
    now = int(time.time() * 1000)
    columns = {"updated_at": now, **values}
    assignments = ", ".join(f"{name}=excluded.{name}" for name in columns if name != "id")
    with WRITE_LOCK, connect() as db:
        db.execute(f"INSERT INTO discogs_sync_state(id, {', '.join(columns)}) VALUES (1, {', '.join('?' for _ in columns)}) ON CONFLICT(id) DO UPDATE SET {assignments}", tuple(columns.values()))


def discogs_status() -> dict:
    with connect() as db:
        state = db.execute("SELECT * FROM discogs_sync_state WHERE id = 1").fetchone()
        count = db.execute("SELECT COUNT(*) AS count FROM records WHERE is_in_collection = 1").fetchone()["count"]
        configured = bool(db.execute("SELECT 1 FROM app_settings WHERE key = 'discogs_token' AND value != ''").fetchone())
    return {"tokenConfigured": configured, "recordCount": count, "sync": dict(state) if state else None}


def fetch_discogs_collection(api: DiscogsClient, username: str) -> list[dict]:
    """Fetch lightweight collection entries; full release data waits for import."""
    collection_items: list[dict] = []
    page = 1
    while True:
        response = api.get(
            f"/users/{quote(username, safe='')}/collection/folders/0/releases",
            page=page, per_page=100, sort="added", sort_order="desc",
        )
        # Show the complete owned collection in the picker. Discogs often marks
        # a 12-inch as "LP" rather than `12\"`, so filtering here hid valid
        # records before the user ever had a chance to choose them.
        collection_items.extend(item for item in response.get("releases", []))
        pages = int((response.get("pagination") or {}).get("pages") or 1)
        if page >= pages:
            break
        page += 1
    return collection_items


def collection_preview() -> dict:
    api = DiscogsClient(discogs_token())
    identity = api.get("/oauth/identity")
    username = identity.get("username")
    if not isinstance(username, str) or not username:
        raise DiscogsAPIError("Discogs did not return an account name for this token.")
    records = []
    for item in fetch_discogs_collection(api, username):
        basic = item.get("basic_information") or {}
        instance_id = item.get("instance_id")
        release_id = basic.get("id")
        if not isinstance(instance_id, int) or not isinstance(release_id, int):
            continue
        artists = ", ".join(str(artist.get("name", "")) for artist in basic.get("artists", []) if artist.get("name"))
        formats = [format_data for format_data in basic.get("formats", []) if str(format_data.get("name", "")).casefold() == "vinyl"]
        records.append({
            "instanceId": instance_id, "releaseId": release_id, "title": basic.get("title") or "Untitled",
            "artists": artists, "year": basic.get("year"), "thumb": basic.get("thumb"),
            "formats": formats,
        })
    return {"username": username, "records": records}


def import_discogs_selection(instance_ids: set[int], progress: Callable[[int, int], None] | None = None) -> dict:
    if not DISCOGS_SYNC_LOCK.acquire(blocking=False):
        raise DiscogsAPIError("A Discogs sync is already in progress.")
    try:
        api = DiscogsClient(discogs_token())
        identity = api.get("/oauth/identity")
        username = identity.get("username")
        if not isinstance(username, str) or not username:
            raise DiscogsAPIError("Discogs did not return an account name for this token.")
        sync_mark = int(time.time() * 1000)
        update_sync_state(username=username, last_started_at=sync_mark, last_error=None, continuation_page=1)
        collection_items = [item for item in fetch_discogs_collection(api, username) if item.get("instance_id") in instance_ids]
        if progress:
            progress(0, len(collection_items))
        imported_records = imported_tracks = 0
        for index, item in enumerate(collection_items, start=1):
            release_id = (item.get("basic_information") or {}).get("id")
            if not isinstance(release_id, int):
                continue
            release = api.get(f"/releases/{release_id}")
            with WRITE_LOCK, connect() as db:
                with db:
                    imported_tracks += import_discogs_record(db, item, release, sync_mark)
            imported_records += 1
            if progress:
                progress(imported_records, len(collection_items))
            update_sync_state(username=username, continuation_page=index)
        completed_at = int(time.time() * 1000)
        update_sync_state(username=username, last_completed_at=completed_at, last_error=None, continuation_page=None)
        return {"username": username, "records": imported_records, "tracks": imported_tracks, "requested": len(instance_ids)}
    except DiscogsAPIError as error:
        update_sync_state(last_error=str(error))
        raise
    finally:
        DISCOGS_SYNC_LOCK.release()


def import_progress() -> dict:
    with IMPORT_PROGRESS_LOCK:
        return dict(IMPORT_PROGRESS)


def start_discogs_import(instance_ids: set[int]) -> None:
    with IMPORT_PROGRESS_LOCK:
        if IMPORT_PROGRESS["state"] == "running":
            raise DiscogsAPIError("A Discogs import is already in progress.")
        IMPORT_PROGRESS.update(state="running", completed=0, total=len(instance_ids), error=None)
    def report(completed: int, total: int) -> None:
        with IMPORT_PROGRESS_LOCK:
            IMPORT_PROGRESS.update(completed=completed, total=total)
    def work() -> None:
        try:
            result = import_discogs_selection(instance_ids, report)
            with IMPORT_PROGRESS_LOCK:
                IMPORT_PROGRESS.update(state="completed", result=result)
        except Exception as error:
            with IMPORT_PROGRESS_LOCK:
                IMPORT_PROGRESS.update(state="failed", error=str(error))
    threading.Thread(target=work, name="discogs-import", daemon=True).start()


def sync_discogs_collection() -> dict:
    """Compatibility helper for a future non-interactive full sync."""
    preview = collection_preview()
    return import_discogs_selection({record["instanceId"] for record in preview["records"]})


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
            settings = {"current_id": state.get("currentId") or "", "history": json.dumps(state.get("history", [])), "matching_settings": json.dumps(state.get("matchingSettings", {})), "state_saved": "1"}
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
        path = urlparse(self.path).path
        if path == "/api/state":
            with connect() as db:
                self.send_json(database_state(db))
            return
        if path == "/api/discogs/status":
            self.send_json(discogs_status())
            return
        if path == "/api/discogs/import-status":
            self.send_json(import_progress())
            return
        if path == "/api/discogs/collection-preview":
            try:
                self.send_json({"ok": True, **collection_preview()})
            except DiscogsAPIError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if path == "/api/health":
            self.send_json({"ok": True, "database": str(DB_PATH)})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/settings/discogs-token":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or not isinstance(payload.get("token", ""), str):
                    raise ValueError("Token must be text.")
                token = payload.get("token", "").strip()
            except (json.JSONDecodeError, ValueError, TypeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
                return
            with WRITE_LOCK, connect() as db:
                if token:
                    db.execute("INSERT INTO app_settings(key, value, updated_at) VALUES ('discogs_token', ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (token, int(time.time() * 1000)))
                else:
                    db.execute("DELETE FROM app_settings WHERE key = 'discogs_token'")
            self.send_json({"ok": True, "configured": bool(token)})
            return
        if path in {"/api/discogs/validate", "/api/discogs/sync"}:
            try:
                token = discogs_token()
                api = DiscogsClient(token)
                identity = api.get("/oauth/identity")
                username = identity.get("username")
                if not isinstance(username, str) or not username:
                    raise DiscogsAPIError("Discogs did not return an account name for this token.")
                if path == "/api/discogs/validate":
                    self.send_json({"ok": True, "username": username})
                    return
                # Fetch identity again within the sync only when a sync is requested;
                # validation remains a cheap, separate action for Settings.
                result = sync_discogs_collection()
                self.send_json({"ok": True, **result})
            except DiscogsAPIError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if path == "/api/discogs/import":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                selected = payload.get("instanceIds") if isinstance(payload, dict) else None
                if not isinstance(selected, list) or not selected:
                    raise ValueError("Select at least one record to import.")
                instance_ids = {item for item in selected if isinstance(item, int)}
                if len(instance_ids) != len(selected):
                    raise ValueError("Invalid Discogs collection selection.")
                start_discogs_import(instance_ids)
                self.send_json({"ok": True, "state": "started"}, HTTPStatus.ACCEPTED)
            except (json.JSONDecodeError, ValueError, TypeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except DiscogsAPIError as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_GATEWAY)
            return
        if path != "/api/state":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        # The CSV state endpoint is intentionally disabled. Step 2 introduces
        # explicit Discogs import endpoints; step 3 replaces this browser client.
        self.send_json({"error": "The legacy CSV state API has been retired."}, HTTPStatus.GONE)

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
