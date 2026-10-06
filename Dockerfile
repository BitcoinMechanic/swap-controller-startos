FROM debian:bookworm-slim AS source
RUN apt-get update -qq && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init && git remote add origin https://github.com/BitcoinMechanic/lightning.git && \
    git fetch --depth=1 origin 81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe && \
    git checkout --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = 81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe && \
    git rev-parse HEAD > tools/blake2b/SOURCE_COMMIT

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY assets/controller.py assets/read_only_rpc.py assets/readiness.py assets/gate_observation.py assets/live_policy.py /app/
COPY --from=source /src/tools/blake2b/ /opt/swap/
COPY --from=source /src/LICENSE /opt/swap/LICENSE
COPY assets/executor.py assets/execution_child.py assets/execution_rpc.py assets/lifecycle.py /app/
COPY assets/recovery.py assets/recovery_inspection.py assets/recovery_workflow.py assets/recovery_actions.py /app/
COPY assets/quote_workflow.py assets/quote_actions.py assets/reverse_quote_workflow.py /app/
RUN python3 -m py_compile /app/controller.py /app/read_only_rpc.py /app/recovery.py /app/recovery_inspection.py
CMD ["python3", "/app/controller.py", "/data", "run"]
