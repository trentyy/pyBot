# syntax=docker/dockerfile:1
# 支援 amd64 / arm64 / armv7（樹莓派）。建置：docker compose build
ARG PYTHON_VERSION=3.11

# ---------- 建置階段：安裝套件到獨立的 venv ----------
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
# armv7 等沒有現成 wheel 的平台需要自行編譯 asyncpg
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt .
RUN /opt/venv/bin/pip install -r requirements.txt

# ---------- 執行階段 ----------
FROM python:${PYTHON_VERSION}-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Taipei
# tzdata：讓 log 時間使用 TZ 時區
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY dc_manager ./dc_manager
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh \
    && mkdir -p data logs config \
    && chown 1000:1000 data logs

# 儀器警報區網接收器（ALARM_HTTP_ENABLED=true 才會監聽）
EXPOSE 8080
# Prometheus 指標（/metrics，一直開著）
EXPOSE 9107
ENTRYPOINT ["entrypoint.sh"]
