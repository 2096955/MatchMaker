# SCUDO Mapping MCP — single image, multiple entrypoints.
#
# The same image runs as the Flask backend, the Ingestion MCP, the
# Match-and-Verify MCP, or the Persistence MCP — selected by the container's
# `command:` in the ECS task definition. We deliberately do NOT build three
# images per the trust-gradient split: the IAM-enforced perimeter (separate
# task roles) and the AST-based TRUST_* smoke gates (Ingestion does not
# import write surfaces) already deliver the boundary. Three images would
# triple the build + ECR storage + cache-miss cost for the same code.
#
# CMD per service in the task def (no rebuild needed):
#   Flask backend  : gunicorn -b 0.0.0.0:5000 -k gthread --threads 4 \
#                    --timeout 300 app:app
#   Ingestion MCP  : python -m scudo_mapping_mcp.ingestion_mcp
#   Match-Verify   : python -m scudo_mapping_mcp.match_verify_mcp
#   Persistence    : python -m scudo_mapping_mcp.persistence_mcp

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/srv/backend \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is for ALB health-check probes from inside the task (and for ad-hoc
# debugging via ECS Exec). Nothing else from the system is needed; the matcher
# is pure-Python over FalkorDB + Bedrock.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Layer caching: dependencies first so a code-only change does not re-pull pip.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && pip install --no-cache-dir \
       gunicorn>=22.0 \
       boto3>=1.34

COPY backend/ ./backend/

WORKDIR /srv/backend

# Defaults; overridden by ECS task env.
ENV MCP_HOST=0.0.0.0 \
    FRAME_SOURCE=mock \
    STORE_BACKEND=falkordb \
    CONFIDENCE_FLOOR=0.80

# Ports each entrypoint listens on (documented; ECS task def maps explicitly).
# Flask         : 5000
# Ingestion MCP : 8001
# Match-Verify  : 8002
# Persistence   : 8003
EXPOSE 5000 8001 8002 8003

# Default CMD is the Flask backend (most common entrypoint for local docker
# run / docker compose); ECS task defs for the three MCPs override.
# gthread for SSE: Flask's WSGI sync workers don't keep streaming connections
# open well. --timeout 300 covers long-running agent SSE runs.
CMD ["gunicorn", \
     "-b", "0.0.0.0:5000", \
     "-k", "gthread", \
     "--threads", "4", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
