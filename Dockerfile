FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py
COPY core /app/core
COPY kcd /app/kcd
COPY tools /app/tools
COPY installer /app/installer
COPY installer_macro.py install.py /app/
COPY templates /app/templates
COPY static /app/static

RUN mkdir -p /app/data

EXPOSE 5000

CMD ["python", "-m", "kcd.server"]
