# Deterministic build for Railway — avoids Nixpacks' uv autodetection
# (which emits a broken `pip install uv==`). Plain pip + pinned requirements
# exported from uv.lock (regenerate with:
#   uv export --no-dev --no-hashes --no-emit-project -o requirements.txt).
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# One-shot: run the digest pipeline and exit (Railway Cron drives the schedule).
CMD ["python", "main.py"]
