# 1. Базовый образ Python
FROM python:3.11

# 2. Установка системных утилит для OCR и языковых моделей (русский + английский)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \             # ← добавлено для распознавания англ. текста
    poppler-utils \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
 && apt-get clean

# 3. Установка Python-зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Копируем весь проект внутрь контейнера
COPY ./app /app
WORKDIR /app

# 5. Запуск бота
CMD ["python", "bot_main.py"]
