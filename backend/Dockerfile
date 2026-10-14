FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /data/snapshots && chown app:app /data/snapshots
WORKDIR /app
COPY requirements.txt requirements-scanner.txt ./
RUN pip install -r requirements.txt \
    && python -m venv /opt/checkov \
    && /opt/checkov/bin/pip install -r requirements-scanner.txt
ENV CHECKOV_BINARY=/opt/checkov/bin/checkov
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1 --timeout-graceful-shutdown 600"]
