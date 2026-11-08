# AnimeClover corrections

Implemented in the local frontend and backend source. Production has not been deployed by this task.

## Authentication and caching

- Admin authentication uses random opaque tokens whose SHA-256 hashes are stored in `admin_sessions`. Signatures made with the old hardcoded key no longer authenticate an administrator.
- Logout deletes the stored session across workers. Changing credentials revokes every existing session and creates a replacement HttpOnly cookie for the current browser.
- Startup keeps an existing renamed administrator instead of recreating an `admin` account.
- A private streaming signing key is read from `SECURITY_SIGNING_KEY`, or generated once in the persistent backend data directory. Do not commit or publicly share that file.
- The service worker only caches an explicit list of public static assets. It removes the previous AnimeClover cache and excludes APIs, admin responses, pages and playback links.
- Direct video relaying requires a signed source URL. Direct and HLS playback tokens both last four hours so ordinary episode playback does not expire after five minutes.

## Indexing and content

- Watch pages validate episode existence and release state before creating page metadata. Missing works and episodes remain distinct from provider outages.
- Series pages render episode links on the server and provide links to subsequent groups of episodes.
- Library pagination has self-referencing canonical URLs. Filter and search variants are marked noindex.
- The persistent catalog inventory is seeded from cached metadata during startup, extended when metadata is saved, and retained when provider caches expire. Sitemaps cover all recorded works and released episode numbers, with 5,000-URL partitions and no 24-episode cutoff.
- Sitemaps omit unknown modification dates rather than changing every date daily. Inventory failures return a retryable error instead of an empty successful sitemap.
- Removed invented review counts and fabricated video publication dates/player URLs. Video rich-result eligibility still requires genuine publication/player metadata and verification in Search Console.
- Imported JSON-LD strings escape `<` so external text cannot terminate the script element.
- Film pages no longer claim every work is dubbed in Egyptian Arabic or produced by Disney. Arabic factual summaries use the available metadata, retaining the original synopsis as expandable text.

## Arabic and English anime names

- Public catalog/detail/schedule results expose `title_english`, `title_ar`, `title_aliases`, and `title_ar_source`. Japanese/native fields are excluded from visible names and SEO; original provider matching identifiers remain available internally for playback resolution.
- Arabic names include 1,050 exact AniList-ID labels from Wikidata, plus curated common names and Arabic spelling variants. Unmapped names use an explicitly tagged phonetic Arabic spelling rather than an invented translated title. See `backend/app/title_data/README.md` for provenance.
- Cards, the cinematic carousel, detail headings, watch pages, and schedules display English and Arabic text with their language and direction set independently. Both names are server-rendered where the page is server-rendered.
- Work/episode title tags, descriptions, Open Graph/Twitter metadata, and structured data include both names. Canonical URLs continue to identify one work/episode; this change does not create duplicate language URLs.
- Arabic exact/partial catalog search resolves to the same provider IDs, preserving pagination and filters. Arabic diacritics and common letter variations are normalized. Existing cache results are localized on read, and search aliases are restored from the persistent catalog during startup.
- Deploy backend and frontend together for the new Arabic name fields to appear everywhere. Google controls result titles and timing after recrawling; this change does not guarantee ranking or a particular result title.

## Responsive footer, discovery guide, and search suggestions

- The footer separates discovery, help, and policies into labelled navigation groups, with readable mobile spacing and a separate copyright row. The homepage guide uses three clear links and genre shortcuts.
- The header search uses a combobox with anime art, English/Arabic names, year/type, keyboard arrows, Enter, Escape, clear, outside dismissal, and full-search access. Requests debounce for 220 ms, cancel superseded requests, and ignore stale responses. Loading, empty and provider failure states are distinct.
- `/api/anime/suggestions` searches the learned catalog first, supports Arabic/English prefixes and common spelling variants, excludes hidden works, and falls back to the provider with a bounded timeout. Suggestion responses use `Cache-Control: no-store`.
- Browser QA used the actual React components and application CSS in a static-file preview, with network fixtures for API response races/errors. It verified 320, 390, 614, 870, and 1440 px widths, real image loading, no document overflow, keyboard navigation, rapid input, and mobile dismissal. It did not modify or deploy the production website.

## Arabic stories and watch-page discussion

- Watch, series, and movie story renderers share the Arabic synopsis. Foreign descriptions and the former expandable original-language block are excluded from the UI. The frontend response boundary also excludes the original synopsis from serialized page props.
- Bilingual headings prioritize the Arabic name visually and retain the English name as secondary text; search and metadata continue to support both.
- Work and episode meta descriptions, social descriptions, and JSON-LD use the same Arabic summary while keeping both Arabic and English names searchable. The canonical work/episode URLs remain unchanged.
- Added ten reviewed Arabic plot summaries, including the reported Black Clover Season 2 work `195604`. Each override is keyed by exact AniList identity and a checksum of the source synopsis; an edited source must be translated again instead of reusing a stale review.
- Other English synopses can be translated locally with the pinned Argos EN→AR model using CTranslate2/SentencePiece on CPU. No translation API key or per-request external translation call is required. Model downloads happen once at install/build time, with a pinned SHA-256 check.
- Translations are cached by work ID, model-cache version, and source-text hash for a year. Duplicate work requests share a generation task; one translation thread and a bounded queue prevent concurrent CPU-heavy jobs. Requests wait at most three seconds; when a model is missing, translation fails, or a job is still running, the UI shows labelled Arabic work facts instead of a foreign or invented plot. A later request uses the finished translation.
- Machine-generated summaries display an Arabic machine-translation label. Reviewed summaries take precedence for matching sources. Model quality, especially uncommon proper names, can require editorial correction.
- The discussion UI separates the rating card and comments, uses labelled multiline inputs, native radio star controls with 44px targets, Arabic status/error messages, and responsive layouts. Failed comments retain their text; failed likes roll back their optimistic count; non-success rating responses are shown as failures.
- Automatic PWA installation prompts are suppressed on `/watch/` pages. The explicit header installation button remains available.

### Translation setup and validation

Docker builds install `backend/scripts/install_arabic_model.py`'s checksum-pinned model under `/opt/animeclover/models/en_ar`, outside the mounted data volume. For a non-Docker backend, install `requirements.txt`, run `python scripts/install_arabic_model.py --destination data/translation/en_ar` from the backend directory, or set `ARABIC_TRANSLATION_MODEL_DIR` to an existing installation. `ARABIC_TRANSLATION_ENABLED=false` disables machine generation while retaining reviewed/provider Arabic content and the Arabic factual fallback.

The model archive is about 88.5 MB; inference uses a CPU model per application worker, loaded on first use. The package/model provenance is [Argos Translate](https://github.com/argosopentech/argos-translate) and its [published package index](https://github.com/argosopentech/argospm-index). The model archive and third-party test dependencies under `.runtime/` are local QA artifacts, not repository source assets.

Validation includes real local-model inference plus persistent-cache reuse, source-checksum invalidation, concurrent translation deduplication, unavailable-model/non-English-input fallbacks, and the reported reviewed synopsis. Browser QA exercised actual React components at 320, 390, 490, 870, and 1440px with intercepted community API fixtures, including ratings, failed/retried comments, replies, failed/retried likes, readable Arabic story text, and absence of watch-page install overlays. No test comments or ratings were posted to the live site.

## Stable public URLs

- Public work and episode URLs continue to use numeric anime identity, not the editable title, artwork, provider stream URL, or database episode row ID.
- `CatalogEntry.canonical_path` stores the first published work path. Upserts update metadata while preserving that path, including after a category change. Existing inventory is pinned before newer caches are imported; the migration is repeatable and retains existing records.
- Shared frontend link generation, canonical metadata, structured-data links, and sitemaps use the stored path. Alternate category pages redirect permanently to the stored route.
- Work metadata is retained independently of cache expiry. If a provider fails, known work pages can use the saved metadata and display a stale-information notice. A temporary episode-list failure does not remove the saved work page. Playback availability is still checked when opening a video.
- Cache deletion and restart do not delete the permanent inventory. Preserve the production database volume and include it in normal backups: recovery of saved identities/paths depends on restoring that database. This persistence applies to public work/episode page URLs; upstream media-file URLs and signed playback tokens retain their normal expiry.
- Regression coverage: `python -m unittest test_catalog_links_search` covers edits, artwork/category changes, cache deletion, restart, legacy migration, provider outage, sitemap paths, and Arabic/English typeahead behavior.

## Analytics

- Playback starts and watch-progress deltas are distinct event types. Progress is measured from the media clock and wall time; seeking does not add watch time.
- Event identifiers make retries idempotent. Historical positive-duration play events are interpreted as progress when reporting counts.
- Overview totals and top lists use the same requested period. Visitor detail is identified as cumulative browser history.
- Tracking includes query parameters and the original document referrer. Visible page time is flushed on visibility changes and page exit.
- Historical page-duration data cannot be reconstructed because the old implementation did not store heartbeat events. The admin page displays this limitation; external iframe players remain outside precise playback measurement.

## Deployment

1. Take the normal production database/volume backup, then deploy the backend and frontend changes together using the existing Docker Compose configuration.
2. Keep the backend data volume persistent. Set a private `SECURITY_SIGNING_KEY` of at least 32 characters or leave it empty to generate the persistent file. Do not use the former hardcoded value.
3. The existing database initializer adds analytics columns and the permanent catalog path column, and creates the new session/catalog tables; it does not delete existing visitor records. Administrators must sign in again after this deployment.
4. Ensure `NEXT_PUBLIC_SITE_URL` is the production origin at build time and `INTERNAL_BACKEND_URL` points to the backend container.
5. Check the homepage, real/missing/upcoming episode pages, admin login/logout/credential change, and all sitemap partitions after deployment. Submit `/sitemap.xml` in Search Console and inspect representative episode URLs.

## Validation

- `npm run test:audit`: frontend regression checks for invalid episode IDs, playback timing, seek handling and private-cache exclusions.
- `npm run test:seo`: bilingual SSR names, Arabic search variants, work/episode metadata, canonicals, and legacy native-name exclusion. Backend: `python -m unittest test_title_localization` with a separate test database/data directory.
- TypeScript checking and `npm run build`.
- Backend regression suite: `test_audit_fixes`, `test_admin_login`, `test_anime_discovery_routes`, `test_stream_cache`, `test_source_http`, `test_video_http`, `test_playback_sources`. Run with a separate test database and data directory.

Test output and the fixture preview server live under `.runtime/`; they are not application deployment sources. The preview fixtures must never replace the production backend.
