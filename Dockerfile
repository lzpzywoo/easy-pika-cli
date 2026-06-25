FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-full.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-full.txt

COPY pikpak_downloader/ ./pikpak_downloader/
COPY main.py ./

ENV PYTHONUNBUFFERED=1
ENV DOWNLOAD_DIR=/data/downloads

VOLUME ["/data/session", "/data/downloads"]

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]
