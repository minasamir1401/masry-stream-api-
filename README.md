# Masry Stream - API & Backend Engine

Asynchronous streaming & metadata engine built with Python 3.11, FastAPI, SQLAlchemy (Async), and PostgreSQL.

## Features

- High throughput FastAPI REST APIs with GZip and HTTP caching headers
- Multi-source scraper pipeline and video stream resolver
- Admin control panel authentication with secure cookies and session store
- Automatic database migrations and SQLite fallback for local testing
- Production-ready Dockerfile optimized for Dokploy and Traefik reverse proxies

## AnimeClover updates

The backend includes persistent public anime URL identities, Arabic/English catalog names and search suggestions, Arabic plot summaries with cached local translation, paginated sitemap inventory, session-based admin authentication, and corrected playback analytics.

The Docker build installs the pinned English-to-Arabic model automatically. For a non-Docker installation, after installing `requirements.txt`, run `python scripts/install_arabic_model.py --destination data/translation/en_ar` from this repository. The model is downloaded once during installation, not during API requests.

See [change and validation notes](docs/AUDIT_FIXES.md), [Arabic synopsis provenance](app/title_data/SYNOPSES.md), and [combined-workspace deployment templates](deployment/README.md). Deploy the matching frontend update from [masry-stream-web](https://github.com/minasamir1401/masry-stream-web) alongside this backend.

## Getting Started

### Local Development

1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Configure environment:
   ```bash
   cp .env.example .env
   ```

3. Run server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
   Interactive API documentation available at `http://localhost:8000/docs`.

### Docker Deployment

```bash
docker build -t masry-stream-api .
docker run -p 8000:8000 masry-stream-api
```

