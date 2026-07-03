# Stub. Finalized in Phase 9: one image that can run any pipeline stage
# (stage as a CLI arg) and the dashboard, fixtures by default.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
