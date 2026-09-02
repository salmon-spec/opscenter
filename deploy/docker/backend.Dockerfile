FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/opscenter/backend

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt

COPY backend/app ./app
COPY agent /opt/opscenter/agent
COPY deploy/observability /opt/opscenter/deploy/observability
COPY frontend/groups.json frontend/services.json /opt/opscenter/frontend/

EXPOSE 9091

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9091"]
