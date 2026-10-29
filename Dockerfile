FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY sispreguaica ./sispreguaica
RUN pip install --no-cache-dir ".[postgres]" \
    && useradd --create-home --uid 1000 sispreguaica \
    && mkdir -p /datos && chown sispreguaica /datos

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER sispreguaica
WORKDIR /datos
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sispreguaica", "servir", "--host", "0.0.0.0", "--puerto", "8000"]
