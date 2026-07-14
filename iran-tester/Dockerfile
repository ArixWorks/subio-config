FROM python:3.12-slim-bookworm

ARG XRAY_VERSION=25.6.8
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && ARCH="$(dpkg --print-architecture)" \
 && case "$ARCH" in amd64) XARCH=64;; arm64) XARCH=arm64-v8a;; *) exit 1;; esac \
 && curl -fsSLo /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XARCH}.zip" \
 && unzip /tmp/xray.zip xray -d /usr/local/bin \
 && chmod 0755 /usr/local/bin/xray \
 && rm -rf /var/lib/apt/lists/* /tmp/xray.zip

RUN groupadd --system subio && useradd --system --gid subio --home /app subio \
 && mkdir -p /data/subs && chown -R subio:subio /data/subs
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --upgrade pip && pip install .
USER subio
EXPOSE 8080
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
