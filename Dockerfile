FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/install_arabic_model.py /tmp/install_arabic_model.py
RUN python /tmp/install_arabic_model.py --destination /opt/animeclover/models/en_ar

COPY . .

RUN mkdir -p /app/data /app/cache/posters

EXPOSE 8000

# Scraping and relaying are I/O bound; two async workers are enough and
# keep per-worker scrape caches from multiplying upstream requests.
ENV DEBUG=false \
    ARABIC_TRANSLATION_MODEL_DIR=/opt/animeclover/models/en_ar \
    WEB_CONCURRENCY=2

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30", "--proxy-headers", "--forwarded-allow-ips=*"]
