# syntax=docker/dockerfile:1
#
# whenwillhazeend.com -- one container: FastAPI + the content-hashed static build.
# Sized for a 1 vCPU / 1 GiB instance (DigitalOcean App Platform, Singapore).

# ---- build the static site (stdlib only) ------------------------------------
FROM python:3.14-slim AS site
WORKDIR /src
COPY app/templates ./app/templates
COPY app/static ./app/static
COPY scripts/build_static.py ./scripts/build_static.py
RUN python scripts/build_static.py --check

# ---- runtime ------------------------------------------------------------------
FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVE_BUILT=1 \
    PORT=8080 \
    WEB_CONCURRENCY=2
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY --from=site /src/public ./public

# never run as root
RUN useradd --create-home --uid 10001 web && chown -R web:web /srv
USER web

EXPOSE 8080
# Two workers: one shared vCPU, so a long simulation in one worker doesn't stall
# every other visitor. --proxy-headers because the platform terminates TLS.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 5"]
