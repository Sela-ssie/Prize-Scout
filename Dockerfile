FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY config/ ./config/
COPY extractor_weights.json ./
COPY --from=frontend /build/dist ./frontend/dist
RUN useradd --create-home scout && mkdir -p /data && chown scout:scout /data
USER scout
ENV DATABASE_URL=sqlite:////data/prizes.db
ENV HOST=0.0.0.0
EXPOSE 8000
CMD ["python", "scripts/serve.py"]
