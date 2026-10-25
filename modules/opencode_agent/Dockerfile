FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/module
WORKDIR /opt/module
COPY modules/opencode_agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && useradd --uid 10001 --create-home agent
COPY --chown=10001:10001 modules/runtime /opt/module/runtime
COPY --chown=10001:10001 modules/opencode_agent /opt/module/opencode_agent
WORKDIR /opt/module/opencode_agent
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
