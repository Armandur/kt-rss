# kt-rss - slim Python 3.12, icke-root, beroenden via uv (spec SS13).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.7

# Beroenden i ett eget lager för bättre bygg-cache.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY kt_rss/ ./kt_rss/

# Icke-root-användare uid 99 / gid 100 = Unraids nobody:users, så att en
# monterad appdata-volym blir skrivbar utan extra chown. /data är volym-mount.
RUN useradd --create-home --uid 99 --gid 100 app \
    && mkdir -p /data && chown -R 99:100 /app /data
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "kt_rss.main:app", "--host", "0.0.0.0", "--port", "8000"]
