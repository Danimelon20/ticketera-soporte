# Etapa 1 (solo para construir): Node arma las islas de React con Vite. No llega a la imagen final
FROM node:26-alpine AS frontend

WORKDIR /frontend

# Primero solo la lista de librerías: si no cambió, Docker reutiliza este paso (npm ci es lo más lento)
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# Etapa 2: la imagen que corre. Solo Python; de la etapa 1 copia únicamente el resultado del build
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /frontend/dist/ static/islands/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
