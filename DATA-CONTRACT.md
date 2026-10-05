# CRATE data contract

`Genres` and `Subgenre / Style` may contain multiple values separated by a semicolon (`;`). Each value is trimmed independently; blanks are ignored.

For the database implementation, model both fields as many-to-many relationships rather than as a single text value:

- `track_genres(track_id, genre_id)`
- `track_styles(track_id, style_id)`

The supplied `genres.json` (15 values) and `styles.json` (757 values) are the authoritative enum sources. Validate imported or manually edited values against those lists before saving records in the future database-backed version.
