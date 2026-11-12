# Lambda container image.
#
# A container image rather than a zip because Phase 3 adds PDF and DOCX parsing
# libraries that comfortably exceed the 250 MB unzipped limit for layers. Moving
# to containers later means rewriting the packaging step mid-project; starting
# here costs nothing.
#
# Base image is pinned to the verified upstream ECR Public digest. AL2023 Lambda
# base images lock $releasever to their snapshot release (2023.12.20261026),
# which carries glibc 2.34-231.amzn2023.0.5 and rpm 4.16.1.3-29.amzn2023.0.8.
# Passing --releasever=latest applies the minimal AL2023 security updates
# (glibc 2.34-231.amzn2023.0.6 and rpm 4.16.1.3-29.amzn2023.0.9) from the
# official Amazon Linux 2023 repository.
FROM public.ecr.aws/lambda/python:3.12@sha256:7236d211c350c63b264efb59f4a257a34acc4a28454f556488981918bb96b394

RUN dnf --releasever=latest upgrade -y \
        glibc \
        glibc-common \
        glibc-langpack-en \
        glibc-minimal-langpack \
        rpm \
        rpm-libs \
    && dnf clean all \
    && rm -rf /var/cache/dnf

COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY app ${LAMBDA_TASK_ROOT}/app

# --no-cache-dir keeps the image small; pip's cache is never read again in a
# read-only Lambda filesystem.
RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}

CMD ["app.lambda_handler.handler"]
