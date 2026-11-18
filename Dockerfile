FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8100 \
    FOOD_AUTH_MODE=jwt FOOD_STORE=mongo

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY food ./food
COPY web ./web
COPY scripts ./scripts
COPY main.py .

EXPOSE 8100
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["python", "main.py"]
