# --- frontend build ---
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- app ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata DATA_DIR=/data FRONTEND_DIST=/srv/frontend/dist
WORKDIR /srv
COPY ml/ ./ml/
COPY backend/ ./backend/
RUN pip install --no-cache-dir ./ml ./backend
COPY --from=web /web/dist ./frontend/dist
VOLUME ["/data"]
EXPOSE 8000
WORKDIR /srv/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
