#!/bin/sh
set -eu

APP_DIR=/opt/parking-monitor
RAW_BASE=https://raw.githubusercontent.com/903xgs/parking-monitor/main
IMAGE=python:3.12-alpine
CONTAINER=parking-monitor

die() { printf '%s\n' "错误：$*" >&2; exit 1; }
fetch() {
    url="$1?v=$(date +%s)"
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$url" -o "$2"
    elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$url"
    else die "需要 curl 或 wget"
    fi
}

[ "$(id -u)" -eq 0 ] || die "请用 root 运行"
command -v docker >/dev/null 2>&1 || die "未找到 docker；请先安装并启动 Docker"
mkdir -p "$APP_DIR/data"
for file in monitor.py install.sh update.sh parking-token config.env.example; do
    fetch "$RAW_BASE/$file" "$APP_DIR/$file"
done
chmod 755 "$APP_DIR/install.sh" "$APP_DIR/update.sh" "$APP_DIR/parking-token"
chmod 644 "$APP_DIR/monitor.py" "$APP_DIR/config.env.example"
cp "$APP_DIR/parking-token" /usr/bin/parking-token
chmod 755 /usr/bin/parking-token

if [ ! -f "$APP_DIR/config.env" ]; then
    cp "$APP_DIR/config.env.example" "$APP_DIR/config.env"
    chmod 600 "$APP_DIR/config.env"
    printf '%s\n' "已安装到 $APP_DIR。请编辑 $APP_DIR/config.env，再重新运行本安装命令。"
    exit 0
fi
chmod 600 "$APP_DIR/config.env"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" --restart unless-stopped \
  --env-file "$APP_DIR/config.env" \
  -v "$APP_DIR/monitor.py:/app/monitor.py:ro" \
  -v "$APP_DIR/data:/app/data" \
  -w /app "$IMAGE" python -u monitor.py >/dev/null
sleep 2
docker exec "$CONTAINER" python /app/monitor.py --check || die "停车 API 验证失败；请检查 config.env 和容器日志"
printf '%s\n' '安装完成，停车 API 验证通过。'
docker logs --tail 20 "$CONTAINER"
