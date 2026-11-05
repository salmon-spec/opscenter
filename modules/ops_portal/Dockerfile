ARG BASE_IMAGE=opscenter/backend:7a6dfdaf
FROM ${BASE_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/opt/module
WORKDIR /opt/module
COPY modules/runtime /opt/module/runtime
COPY modules/ops_portal /opt/module/ops_portal
WORKDIR /opt/module/ops_portal
USER 10001:10001
EXPOSE 8080
CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
