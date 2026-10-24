FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AUTOMATION_DATA_DIR=/data
WORKDIR /app
COPY automation/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home automation && mkdir /data && chown 10001:10001 /data
COPY automation /app/modules/automation
COPY runtime /app/runtime
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "modules.automation.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
