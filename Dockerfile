FROM python:3.11-slim

# System deps:
# - ffmpeg: render & mux audio/video
# - ttf fonts: allow Pillow to render text (captions)
# - curl: healthcheck/debug (optional)
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core ttf-dejavu \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY app.py /app/app.py
RUN pip install --no-cache-dir -e .
COPY input /app/input
COPY config.yaml /app/config.yaml
COPY IMPLEMENTATION.md /app/IMPLEMENTATION.md
COPY .env.example /app/.env.example

EXPOSE 8000

CMD ["python", "app.py"]

