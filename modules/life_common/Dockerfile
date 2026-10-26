ARG BACKEND_IMAGE=opscenter/backend:latest
FROM ${BACKEND_IMAGE}
ARG MODULE_NAME
ENV PYTHONPATH=/opt/module PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LIFE_MODULE=${MODULE_NAME}
WORKDIR /opt/module
RUN useradd --uid 10001 --create-home life
COPY --chown=10001:10001 modules/runtime /opt/module/runtime
COPY --chown=10001:10001 modules/life_common /opt/module/life_common
COPY --chown=10001:10001 modules/${MODULE_NAME} /opt/module/${MODULE_NAME}
RUN chmod -R a+rX /opt/module
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "life_common.run"]
