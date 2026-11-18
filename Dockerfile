# Imagen única: compila el portal y lo sirve junto con la API en un solo puerto (8000 dentro del contenedor)

FROM node:22-slim AS portal
WORKDIR /portal
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORTAL_DIR=/app/portal
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=portal /portal/dist ./portal
EXPOSE 8000
# --proxy-headers: detrás de Caddy/nginx respeta https en las URLs que arma
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
