# SCUDO Lambda container image.
# Base: AWS-published Python 3.13 runtime — handles all Lambda integration.
FROM public.ecr.aws/lambda/python:3.13

# Install only what the runtime needs (no LlamaIndex, no pandas — the lambda
# uses the in-memory CDAO mock, not Neptune/sidecar).
COPY requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt

# Build context = backend/scudo/. Copy as a Python package so
# `scudo.lambda_handler.handler` resolves at import time.
COPY . ${LAMBDA_TASK_ROOT}/scudo/

# Lambda entry point.
CMD ["scudo.lambda_handler.handler"]
