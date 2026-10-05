FROM ghcr.io/astral-sh/uv:0.12.17@sha256:10787c682e4184e4f290de1171fd4703dc63de99221f10fe1c99002ce7fa9acc AS uv

FROM node:24-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS web
WORKDIR /app/web
COPY text-to-sql-demo/web/package.json text-to-sql-demo/web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY text-to-sql-demo/web/index.html text-to-sql-demo/web/tsconfig.json text-to-sql-demo/web/vite.config.ts ./
COPY text-to-sql-demo/web/src ./src
COPY text-to-sql-demo/web/public ./public
RUN npm run build

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY nodestep /app/nodestep
COPY nodeartifact /app/nodeartifact
WORKDIR /app/text-to-sql-demo
COPY text-to-sql-demo/pyproject.toml text-to-sql-demo/uv.lock text-to-sql-demo/README.md text-to-sql-demo/LICENSE ./
COPY text-to-sql-demo/src ./src
RUN uv sync --locked --no-dev --no-editable
COPY text-to-sql-demo/scripts/make_demo_db.py ./scripts/
RUN /opt/venv/bin/python scripts/make_demo_db.py

FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
RUN groupadd --system --gid 10001 text-to-sql-demo \
 && useradd --system --uid 10001 --gid text-to-sql-demo --home-dir /app --no-create-home --shell /usr/sbin/nologin text-to-sql-demo \
 && install -d -o text-to-sql-demo -g text-to-sql-demo /data
COPY --from=build /opt/venv /opt/venv
COPY --from=web /app/web/dist /app/web/dist
COPY --from=build /app/text-to-sql-demo/data/databases /app/demo
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TEXT_TO_SQL_DEMO_DATA_DIR=/data \
    TEXT_TO_SQL_DEMO_WEB_DIST=/app/web/dist
WORKDIR /app
USER text-to-sql-demo
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/databases', timeout=4)"]
ENTRYPOINT ["/bin/sh", "-c", "mkdir -p \"$TEXT_TO_SQL_DEMO_DATA_DIR/databases\" && cp /app/demo/demo_shop.sqlite /app/demo/demo_shop.json \"$TEXT_TO_SQL_DEMO_DATA_DIR/databases/\" && exec text-to-sql-demo \"$@\"", "text-to-sql-demo"]
CMD ["serve", "--host", "0.0.0.0"]
