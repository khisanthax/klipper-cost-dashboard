FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask

COPY app.py /app/app.py
COPY core /app/core
COPY templates /app/templates

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["python", "app.py"]
