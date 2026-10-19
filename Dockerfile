FROM python:3.13-slim

# Dependencies come from pyproject so the image and the test environment cannot
# drift apart. uv resolves them; nothing is hand-listed here.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml README.md ./
COPY app ./app
COPY migrations ./migrations
# The import check is load-bearing: installing from a context without `app/`
# succeeds and produces an empty package that only fails at runtime.
RUN uv pip install --system --no-cache ".[postgres]" uvicorn \
    && python -c "import app.api.http, app.db.postgres, app.db.postgres_orders"

RUN useradd --create-home --uid 10001 ops \
    && mkdir -p /srv/data \
    && chown -R ops:ops /srv/data /srv/app
USER ops
ENV ORDER_DB=/srv/data/orders.db \
    REVIEW_DB=/srv/data/reviews.db \
    USER_DB=/srv/data/users.db \
    INVENTORY_DB=/srv/data/inventory.db \
    WANT_LIST_DB=/srv/data/wants.db

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health').read()"
CMD ["uvicorn", "app.api.http:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
