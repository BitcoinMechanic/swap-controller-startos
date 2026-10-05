FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY assets/controller.py assets/read_only_rpc.py /app/
RUN python3 -m py_compile /app/controller.py /app/read_only_rpc.py
CMD ["python3", "/app/controller.py", "/data", "run"]
