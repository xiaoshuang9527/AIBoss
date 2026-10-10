# ============================================================
# 后端镜像
# ------------------------------------------------------------
# 说明：镜像内同时安装了 Chromium，这样容器里也能启动带界面的
# 专用浏览器进程。扫码登录需要图形界面，容器部署时请通过
# X11 转发 / VNC 暴露显示，或改为在宿主机完成一次扫码后
# 把 chrome-profiles 目录挂载进容器。
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Chromium + 中文字体：爬虫需要真实浏览器，字体缺失会导致页面渲染异常
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-noto-cjk \
        fonts-liberation \
        ca-certificates \
        curl \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r /app/backend/requirements.txt \
    && pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        "requests>=2.28.0,<3.0.0" "websocket-client>=1.6.0,<2.0.0"

# 项目代码
COPY backend /app/backend
COPY boss-zhipin-scraper /app/boss-zhipin-scraper

# 工作区与挂载点
RUN mkdir -p /app/backend/storage
VOLUME ["/app/backend/storage"]

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/system/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "bzp_workbench.main:app", "--host", "0.0.0.0", "--port", "8000"]
