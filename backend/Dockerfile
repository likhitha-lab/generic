# Production image for Cloud Run. Multi-stage: build wheels/install deps in
# a full image (needs a C toolchain for some transitive deps), then copy only
# the installed packages + app code into a slim runtime image - keeps the
# shipped image well under half the size of installing everything in one stage.

# ---- builder ----
FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
# --- Azure SQL only ---
# pyodbc in requirements.txt installs fine from a wheel with no extra system
# packages, but *connecting* to Azure SQL at runtime needs the msodbcsql18
# ODBC driver, which isn't installed by pip. If you use the Azure SQL
# backend, uncomment this block (and its runtime-stage twin below):
# RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
#     && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
#     && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
#     && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
#     && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- runtime ----
FROM python:3.12-slim

# Non-root: Cloud Run doesn't require this, but running containers as root is
# an unnecessary privilege-escalation surface if the container is ever
# compromised via a dependency vulnerability.
#
# Home dir is /home/app, NOT /app (the code WORKDIR) - `pip install --user`
# in the builder stage put packages under a user-site path keyed off the
# home directory, and that path is auto-added to sys.path only if the
# running user's home matches where those packages actually got copied to.
RUN groupadd --system app && useradd --system --gid app --home-dir /home/app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

# --- Azure SQL only (runtime half of the builder-stage block above) ---
# RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
#     && curl -sSL -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
#     && dpkg -i packages-microsoft-prod.deb && rm packages-microsoft-prod.deb \
#     && apt-get update && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
#     && rm -rf /var/lib/apt/lists/*

COPY --from=builder /root/.local /home/app/.local
COPY app/ ./app/
COPY static/ ./static/

RUN chown -R app:app /app /home/app/.local
USER app

ENV PATH=/home/app/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Documents the default; Cloud Run injects the real PORT at runtime and the
# CMD below always binds to whatever that is, defaulting to 8080 if unset
# (e.g. running this image outside Cloud Run, such as `docker run -p 8080:8080`).
EXPOSE 8080

# gunicorn manages a small pool of uvicorn worker processes - more resilient
# than a single bare `uvicorn` process (a worker that hangs or leaks memory
# gets recycled instead of taking the whole container down with it).
# --timeout 0 disables gunicorn's own worker timeout: Gemini calls and
# PDF/DOCX generation can legitimately take longer than the default 30s, and
# Cloud Run's own request timeout (set at deploy time) is the right place to
# bound total request duration, not gunicorn silently killing the worker
# mid-request.
#
# WEB_CONCURRENCY (default 2) is configurable, not hardcoded: with a real
# database (Postgres/Neon - the recommended production setup, see
# DEPLOYMENT_GCP.md), 2 workers is correct and safe. If you're deliberately
# still running DATABASE_URL=sqlite:... (ephemeral, single-writer, NOT
# recommended beyond early testing - see DEPLOYMENT_GCP.md), set
# WEB_CONCURRENCY=1 as a Cloud Run env var: SQLite has no real concurrent-
# writer story, and 2+ workers sharing one file is a correctness risk, not
# just the startup race init_db.py already tolerates.
ENV WEB_CONCURRENCY=2
CMD ["sh", "-c", "gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-2} --timeout 0 --access-logfile - --error-logfile -"]
