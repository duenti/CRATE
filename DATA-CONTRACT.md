# CRATE data contract

## Core model

CRATE models the physical collection before it models DJ choices:

- A `record` is one owned physical Discogs collection instance. Its stable remote identity is `discogs_instance_id`; two copies of the same Discogs release remain distinct records.
- A `record` belongs to a Discogs `release` (`discogs_release_id`) and can have a master release, formats, labels, artists, genres and styles.
- A `track` belongs to exactly one record. Its Discogs position is text, not a number, so positions such as `A`, `A1`, `B2`, `AA1` and `2-1` remain intact.
- A crate is a selected list of tracks. A set is a performance history containing tracks from a crate or the wider collection.

Discogs release data and user-owned collection data are separate. The database therefore stores both the owned-instance ID and the release ID. The Personal Access Token will be stored locally in `app_settings` through Settings in step 2.

## Local database

`app/data/crate.db` contains:

- `records`, `record_formats`, `artists`, `record_artists`, `labels`, `record_labels`, `record_genres`, `record_styles`: imported collection and release metadata.
- `tracks`, `track_artists`: release-specific tracklists and per-track DJ data.
- `genres`, `styles`, `tags`, `moods`, `grooves`, `vocals`, `set_roles`, `intros`, `outros`, plus `track_*` tables: controlled metadata and DJ annotations.
- `crates`, `crate_tracks`, `sets`, `set_plays`, `set_track_stats`, `track_stats`: selections and performance history.
- `discogs_sync_state`: username, progress marker and most recent sync error. It deliberately does not store the token.
- `app_settings`: local application preferences and, in step 2, the locally stored token.

## Schema migration policy

Schema version 2 is an intentional clean break from the CSV model. On the first launch of this branch it preserves `app_settings` and removes legacy tracks, crates, sets, statistics and CSV metadata. The pre-migration database backup is the recovery point.
