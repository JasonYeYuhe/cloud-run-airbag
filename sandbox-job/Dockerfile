# Airbag sandbox job — a MINIMAL python+pytest image that runs the LLM-authored regression test in
# isolation (see runner.py). No autosre, no secrets, no app code — inputs arrive per-execution as
# base64 env vars, and the job runs under a zero-permission SA with NO network egress.
FROM python:3.12-slim

RUN pip install --no-cache-dir "pytest~=8.3"

WORKDIR /app
COPY runner.py /app/runner.py

# no network is needed at runtime (pytest runs on files written from env); egress is blocked by the
# job's VPC config regardless.
ENTRYPOINT ["python", "/app/runner.py"]
