# syntax=docker/dockerfile:1
FROM python:3.11-slim

ARG DEBIAN_FRONTEND=noninteractive
# Чтобы пересобрало слой и не тянуло старый кеш
ARG APT_FORCE_REBUILD=2025-08-10

# Лёгкая установка OCR/PDF без dev-пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    poppler-utils \
    libglib2.0-0 libsm6 libxext6 libxrender1 \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Python-зависимости
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Проект
WORKDIR /app
COPY . /app

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot_main.py"]
