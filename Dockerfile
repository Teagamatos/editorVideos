FROM python:3.11-slim

# ── Sistema ─────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    wget \
    && rm -rf /var/lib/apt/lists/*

# ── Fonte Montserrat ───────────────────────────────
RUN mkdir -p /usr/share/fonts/truetype/montserrat \
    && wget -q https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-Bold.ttf \
    -O /usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf \
    && fc-cache -fv

# ── App ─────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/tmp && chmod 777 /app/tmp

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}