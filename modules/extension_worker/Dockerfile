FROM opscenter/backend:5.5.9-test-retire-cert-legacy

ENV PYTHONPATH=/opt/opscenter/backend

COPY modules/extension_worker/main.py /opt/opscenter/extension_worker.py

CMD ["uvicorn", "extension_worker:app", "--app-dir", "/opt/opscenter", "--host", "0.0.0.0", "--port", "8080"]
