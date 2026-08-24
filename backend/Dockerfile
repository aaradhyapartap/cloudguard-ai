# Lambda container image.
#
# A container image rather than a zip because Phase 3 adds PDF and DOCX parsing
# libraries that comfortably exceed the 250 MB unzipped limit for layers. Moving
# to containers later means rewriting the packaging step mid-project; starting
# here costs nothing.
FROM public.ecr.aws/lambda/python:3.12

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY app ${LAMBDA_TASK_ROOT}/app

# --no-cache-dir keeps the image small; pip's cache is never read again in a
# read-only Lambda filesystem.
RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}

CMD ["app.lambda_handler.handler"]
