# Arabic anime names

`anime_titles.json` contains 1,050 Arabic labels joined by the exact AniList anime identifier (Wikidata property P8729), retrieved on 2026-11-08. The `source` field records the join used. Names are metadata, not plot translations.

Source: https://www.wikidata.org/wiki/Property:P8729

Query: `SELECT ?id ?ar ?en WHERE { ?work wdt:P8729 ?id; rdfs:label ?ar. FILTER(LANG(?ar) = "ar") OPTIONAL { ?work rdfs:label ?en. FILTER(LANG(?en) = "en") } }`

Common Arabic audience names in `services/title_localization.py` take precedence. Provider Arabic synonyms are also supported. Where an Arabic label is unavailable, a phonetic rendering is explicitly marked `title_ar_source=phonetic`; it is not represented as an official translated title.

Arabic exact and partial searches use AniList identifiers from this inventory and the persistent learned catalog. The upstream API applies pagination, sorting, and genre/year filters to those identifiers. Native and romanized provider matching fields remain internal to source resolution; public display selects English/Latin and Arabic names.
