# LiaraPilot agent — plan B for deploying the agent itself on Liara.
# Plan A is the "python" platform via liara.json (see docs/DEPLOY_AGENT.md); the
# python buildpack's build/run detection is UNVERIFIED, so this Dockerfile is the
# deterministic fallback ("docker" IS a VERIFIED liara.json platform value).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Product code + assets (demo mode serves the bundled fixtures)
COPY agent ./agent
COPY app ./app
COPY config ./config
COPY data ./data
COPY deployers ./deployers
COPY inspector ./inspector
COPY llm ./llm
COPY web ./web
COPY fixtures ./fixtures
COPY samples ./samples

# Single exposed port; Liara injects the app port via the platform config.
EXPOSE 3000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
