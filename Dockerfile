FROM python:3.12-slim

# System deps: tesseract for OCR, espeak-ng for voice playback
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000
CMD ["python", "web/backend.py"]
