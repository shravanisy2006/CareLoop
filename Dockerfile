# Container image for Google Cloud Run (or any container host).
# Build:  docker build -t careloop .
# Run:    docker run -p 8080:8080 -e CARELOOP_SECRET_KEY=... careloop
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY careloop ./careloop

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 careloop && mkdir -p /data && chown careloop /data
USER careloop

# Production mode refuses to start without CARELOOP_SECRET_KEY.
ENV CARELOOP_ENV=production \
    CARELOOP_SECURE_COOKIES=true \
    CARELOOP_SQLITE_PATH=/data/careloop.db \
    PORT=8080

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn careloop.web.app:create_app --factory --host 0.0.0.0 --port ${PORT} --proxy-headers"]
