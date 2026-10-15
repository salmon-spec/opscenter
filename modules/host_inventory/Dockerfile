FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/module
WORKDIR /opt/module
RUN pip install --no-cache-dir --disable-pip-version-check fastapi==0.115.6 uvicorn==0.34.0 psycopg[binary]==3.2.3 PyJWT==2.10.1 cryptography==43.0.3
COPY modules/runtime /opt/module/runtime
COPY modules/host_inventory /opt/module/host_inventory
WORKDIR /opt/module/host_inventory
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
