# ---- web UI ---------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API + engine -----------------------------------------------------------
FROM python:3.12-slim
# Install every database driver (Oracle, Snowflake, Databricks, Redshift, Hive, ...).
# Build with --build-arg CONNECTORS=false for a smaller image.
ARG CONNECTORS=true
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DATA_ROOT=/data WORK_DIR=/var/lib/dc/work FRONTEND_DIR=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$CONNECTORS" = "true" ]; then \
         grep -v pyodbc requirements-connectors.txt > /tmp/conn.txt && pip install --no-cache-dir -r /tmp/conn.txt; \
       fi
COPY backend/ ./
COPY --from=ui /ui/dist /app/frontend/dist
RUN useradd -m -u 1000 app && mkdir -p /data /var/lib/dc && chown -R app /data /var/lib/dc
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
