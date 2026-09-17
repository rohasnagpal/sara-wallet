# Sara AI Wallet — developer-evaluation image.
#
# This packages the same backend + frontend you'd run with `uvicorn
# main:app` locally. It's meant for quickly trying Sara without setting
# up a Python virtualenv, not as a production/desktop deployment target —
# see docs/architecture.md for how Sara is actually meant to run
# (locally, on your own machine, with keys that never leave it).
FROM python:3.12-slim

# Match backend/README's supported runtime note: Python 3.12 only, the
# security-fixed LiteLLM release in requirements-lock.txt doesn't support 3.14.

WORKDIR /app

COPY backend/requirements-lock.txt backend/requirements-lock.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r backend/requirements-lock.txt

COPY . .

# Sara reads config from repo-root .env.local (see backend/app/core/config.py).
# The image ships only the tracked .env template; mount or bake in your own
# .env.local at runtime (docker-compose.yml does this for you).
RUN cp -n .env .env.local || true

WORKDIR /app/backend

EXPOSE 8888

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8888"]
