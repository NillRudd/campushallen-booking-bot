FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    STATE_DIR=/app/state
WORKDIR /app

# Resolve runtime dependencies before copying frequently edited application source.
COPY pyproject.toml /tmp/pyproject.toml
RUN python -c 'import tomllib; print("\n".join(tomllib.load(open("/tmp/pyproject.toml", "rb"))["project"]["dependencies"]))' > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

COPY pyproject.toml README.md alembic.ini config.example.yaml ./
COPY src ./src
COPY migrations ./migrations
COPY scripts/dev-entrypoint.sh /usr/local/bin/dev-entrypoint
RUN pip install --no-deps -e . \
    && mkdir -p /app/state \
    && chown app:app /app/state \
    && chmod 700 /app/state \
    && chmod 755 /usr/local/bin/dev-entrypoint

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3)'
ENTRYPOINT ["dev-entrypoint"]
CMD ["uvicorn", "campushallen.web.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
