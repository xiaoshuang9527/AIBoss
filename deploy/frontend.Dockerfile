# ============================================================
# 前端镜像：Node 构建 -> Nginx 静态托管
# ============================================================
FROM node:22-slim AS builder

WORKDIR /app

# 国内镜像加速（保留官方源作为兜底可自行修改）
RUN npm config set registry https://registry.npmmirror.com/

COPY frontend/package.json frontend/package-lock.json* /app/
# --ignore-scripts：部分环境禁止 postinstall 启动子进程；
# esbuild 的平台二进制来自 optionalDependencies，无需 postinstall
RUN npm install --no-audit --no-fund --ignore-scripts

COPY frontend /app
RUN npx vite build


FROM nginx:1.27-alpine

# 时区与中文环境
ENV TZ=Asia/Shanghai
RUN apk add --no-cache tzdata curl && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone

COPY --from=builder /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
