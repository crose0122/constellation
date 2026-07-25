# Memory Vault — the pipeline + Brain web server in one image.
FROM python:3.12-slim

# System libraries: OpenCV/InsightFace need these at runtime; git for nothing
# fancy, just occasionally handy. Kept minimal.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libgl1 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps. onnxruntime + insightface power face recognition on CPU;
# reverse_geocoder does offline geocoding; pillow-heif reads iPhone HEIC.
RUN pip install --no-cache-dir \
        pillow pillow-heif imagehash requests \
        insightface onnxruntime opencv-python-headless \
        reverse_geocoder numpy

COPY scripts/ /app/scripts/
COPY schema/ /app/schema/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV MEMORYVAULT_LIBRARY_ROOT=/data/library \
    MEMORYVAULT_VAULT_MODE=dir \
    MEMORYVAULT_VAULT_MOUNT=/data/vault \
    MEMORYVAULT_TAG_SCHEMA=/app/schema/tag-schema.json \
    MEMORYVAULT_OLLAMA_URL=http://ollama:11434/api/generate \
    PYTHONUNBUFFERED=1

EXPOSE 8484
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["brain"]
