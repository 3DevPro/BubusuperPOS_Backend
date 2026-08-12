FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000


# --proxy-headers/--forwarded-allow-ips: this runs behind Caddy in prod (see
# infra/Caddyfile's reverse_proxy) — without them request.base_url reports
# the internal backend:8000 address instead of the public domain, which
# would break the absolute URLs products.upload_product_image builds.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
