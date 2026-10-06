# CRATE data contract

`Genres` and `Subgenre / Style` may contain multiple values separated by a semicolon (`;`). Each value is trimmed independently; blanks are ignored.

For the database implementation, model both fields as many-to-many relationships rather than as a single text value:

- `track_genres(track_id, genre_id)`
- `track_styles(track_id, style_id)`

The supplied `genres.json` (15 values) and `styles.json` (757 values) are the authoritative enum sources. The local SQLite database seeds `genres` and `styles` from those files.

## Local database

CRATE stores its local state in `app/data/crate.db`. The main tables are:

- `tracks`: the record, DJ fields and Spotify audio-feature columns.
- `genres`, `styles`, `tags` plus their `track_*` join tables: controlled and free multi-value metadata.
- `crates`, `crate_tracks`: saved selections.
- `sets`, `set_plays`: active or completed sessions and the actual order of tracks played.
- `set_track_stats`, `track_stats`: statistics derived from play history, per set and globally.
- `app_settings`: current track and local UI state.

An active set is a row in `sets` with `status = 'active'`; ending it changes the status to `finished`. `set_plays` is the source of truth for a track played in a set.
