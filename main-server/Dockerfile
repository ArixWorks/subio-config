FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN groupadd --system subio && useradd --system --gid subio --home /app subio
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .

USER subio
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
