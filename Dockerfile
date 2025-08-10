# 1) Базовый образ
FROM python:3.11

# 2) Системные пакеты для OCR/PDF (лёгкая установка без dev-пакетов)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    poppler-utils \
    libglib2.0-0 libsm6 libxext6 libxrender1 \
 && rm -rf /var/lib/apt/lists/*

# 3) Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Копируем проект
COPY . /app
WORKDIR /app

# 5) Логи без буферизации
ENV PYTHONUNBUFFERED=1

# 6) Запуск
CMD ["python", "bot_main.py"]
