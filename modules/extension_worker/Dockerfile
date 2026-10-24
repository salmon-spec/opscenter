ARG BACKEND_IMAGE=opscenter/backend:local
FROM ${BACKEND_IMAGE}

COPY backend/app /opt/opscenter/backend/app

ENV PYTHONPATH=/opt/opscenter/backend

COPY modules/extension_worker/main.py /opt/opscenter/extension_worker.py

CMD ["uvicorn", "extension_worker:app", "--app-dir", "/opt/opscenter", "--host", "0.0.0.0", "--port", "8080"]
