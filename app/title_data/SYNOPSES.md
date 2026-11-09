# Arabic synopsis provenance

`synopses_ar.json` contains reviewed Arabic summaries keyed by exact AniList anime ID. The source URL identifies the corresponding work. `source_hash` is the first 24 hexadecimal characters of SHA-256 over the synopsis normalized by `arabic_descriptions.clean_synopsis`.

The reported work `195604` was checked against its actual AniList synopsis on 2026-11-08 and translated into Arabic with consistent names (أستا، يونو، فرسان السحر، مملكة سبيد، الثالوث المظلم). Naruto (`20`) and Sword Art Online (`11757`) were checked against their AniList synopses after reproducing the model's English-copying failure on 2026-11-09. These summaries describe the plots of the corresponding works.

When the normalized source changes, the override is not reused. New English text is processed by the local machine translator and explicitly labelled as machine translation in the interface. Editors can add or revise reviewed summaries and their source checksums in this file's sibling JSON data.
