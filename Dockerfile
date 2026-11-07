FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    HOME=/data \
    SEESTAR_KEY_PATH=/data/seestar.pem

RUN pip install --no-cache-dir "aiohttp>=3.9" "cryptography>=42" "Pillow>=10"

COPY src /app/src
COPY web /app/web

EXPOSE 8731

CMD ["python", "-m", "seestar_desk", "--host", "0.0.0.0", "--port", "8731", "--no-browser"]
