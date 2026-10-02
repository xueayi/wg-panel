# ── 阶段 1：构建前端（产出静态资源，不进运行镜像的任何依赖） ──
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── 阶段 2：运行时（只留 python + 静态资源） ──
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WGP_DATA_DIR=/data \
    WGP_AGENT=/app/agent/wgagent.py \
    WGP_STATIC_DIR=/app/frontend/dist \
    WGP_SSH_KEY=/ssh/id_ed25519

# openssh-client 只是排障备用；面板走 paramiko，不依赖它
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY agent/ ./agent/
COPY --from=web /web/dist ./frontend/dist

RUN useradd --create-home --uid 1000 wgpanel \
    && mkdir -p /data \
    && chown -R wgpanel:wgpanel /data
USER wgpanel

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app/backend"]
