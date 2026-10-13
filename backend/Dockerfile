FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace

# Install the CPU build first so the project's torch>=2.4 requirement is
# satisfied without pulling CUDA libraries into the development image.
RUN python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.4"

COPY backend/pyproject.toml /workspace/backend/
COPY backend/app /workspace/backend/app
COPY backend/scripts /workspace/backend/scripts
COPY backend/configs /workspace/backend/configs
COPY backend/tests /workspace/backend/tests

WORKDIR /workspace/backend
RUN python -m pip install --no-cache-dir -e ".[dev,cloud]"

EXPOSE 8000
CMD ["python", "-m", "scripts.serve", "--host", "0.0.0.0", "--reload"]
