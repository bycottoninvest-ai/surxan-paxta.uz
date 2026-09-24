FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=5000
WORKDIR /app
# rclone: independent (off-server) backup copies
RUN apt-get update && apt-get install -y --no-install-recommends rclone ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY surxon ./surxon
COPY templates ./templates
COPY static ./static
RUN useradd -m surxon && mkdir -p /data && chown surxon /data
USER surxon
ENV DATA_DIR=/data
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:5000/health')"
# --preload: schema migration and first-run seed happen once, before workers fork
CMD ["gunicorn", "--preload", "--workers", "3", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]
