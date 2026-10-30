FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py extractor.py schema.py checks.py ./
COPY static ./static

# Run as a non-root user
RUN useradd --create-home appuser && mkdir results && chown appuser results
USER appuser

EXPOSE 8000
# Inside the container we listen on all interfaces; publish the port to localhost only:
#   docker run --env-file .env -p 127.0.0.1:8000:8000 invoice-ai
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
