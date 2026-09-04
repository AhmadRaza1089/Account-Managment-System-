FROM python:3.12-slim

# Keeps the image small and logs unbuffered so `docker logs` shows output live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml README.md ./
COPY account_manager ./account_manager
# Includes the MySQL driver so the same image works for both the SQLite
# and MySQL compose profiles.
RUN pip install --no-cache-dir ".[mysql]"

# By default the data lives in a volume so it survives `docker run --rm`.
ENV DATABASE_URL=sqlite:////data/account_manager.db
VOLUME ["/data"]

# Run pending migrations, then whatever command was asked for.
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["account-manager", "--help"]
