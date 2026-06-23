# SCUDO Lambda container image.
# Base: AWS-published Python 3.13 runtime — handles all Lambda integration.
FROM public.ecr.aws/lambda/python:3.13

# Runtime deps (now includes falkordb for the real cost-ladder path).
COPY scudo/requirements-lambda.txt .
RUN pip install --no-cache-dir -r requirements-lambda.txt

# Build context = backend/ (parent), so BOTH packages land in the image:
#   scudo             — Lambda handler + Strands orchestrator (Runtime A)
#   scudo_mapping_mcp — the cost-ladder matcher over FalkorDB (Runtime B)
# This lets /run run the real matcher via scudo.matcher_bridge.
COPY scudo/ ${LAMBDA_TASK_ROOT}/scudo/
COPY scudo_mapping_mcp/ ${LAMBDA_TASK_ROOT}/scudo_mapping_mcp/

# Lambda entry point.
CMD ["scudo.lambda_handler.handler"]
