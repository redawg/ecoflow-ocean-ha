# EcoFlow Ocean web dashboard — Podman / Docker (RHEL-friendly)
# Build from repository root:
#   podman build -f web/Containerfile -t ecoflow-ocean-web:latest .
#
# Run:
#   podman run -d --name ecoflow-ocean-web \
#     -p 8080:8080 \
#     -v ecoflow-ocean-data:/data:Z \
#     --env-file web/.env \
#     ecoflow-ocean-web:latest

FROM registry.access.redhat.com/ubi9/python-312:latest

USER 0
WORKDIR /app

# Install deps as root, then drop privileges.
COPY web/requirements.txt /app/web/requirements.txt
RUN pip install --no-cache-dir -r /app/web/requirements.txt

# Bundle the reverse-engineered client + web app.
COPY custom_components/ecoflow_ocean/pyecoflowocean /app/custom_components/ecoflow_ocean/pyecoflowocean
COPY web/backend /app/web/backend
COPY web/frontend /app/web/frontend

RUN mkdir -p /data \
 && chown -R 1001:0 /app /data \
 && chmod -R g=u /app /data

USER 1001
WORKDIR /app/web
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONPATH=/app/web

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8080"]
