FROM python:3.12-slim

# 国内网络可在 .env 里设置 PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1

WORKDIR /app
COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY bridge/app ./app
COPY web ./web

ENV WEB_DIR=/app/web DATA_DIR=/data HOST=0.0.0.0 PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/health',timeout=4)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
