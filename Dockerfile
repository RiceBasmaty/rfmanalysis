# RFM Studio — one image for both Cloud Run services (APP_MODE=full for the app, APP_MODE=share for share links).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY rfm_engine ./rfm_engine
COPY app ./app

# run as an unprivileged user
RUN useradd --system --uid 10001 rfm && chown -R rfm /srv
USER rfm

# One worker on purpose: background jobs and their locks live in this process (see docs/DEPLOY.md).
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 75
