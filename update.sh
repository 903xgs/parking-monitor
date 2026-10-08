#!/bin/sh
set -eu

APP_DIR=/opt/parking-monitor
RAW_BASE=https://raw.githubusercontent.com/903xgs/parking-monitor/main
IMAGE=python:3.12-alpine
CONTAINER=parking-monitor
FILES="monitor.py install.sh update.sh parking-token config.env.example"

die() { printf '%s\n' "错误：$*" >&2; exit 1; }
fetch() {
    url="$1?v=$(date +%s)"
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$url" -o "$2"
    elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$url"
    else die "需要 curl 或 wget"
    fi
}
restart() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" --restart unless-stopped \
      --env-file "$APP_DIR/config.env" \
      -v "$APP_DIR/monitor.py:/app/monitor.py:ro" \
      -v "$APP_DIR/data:/app/data" \
      -w /app "$IMAGE" python -u monitor.py >/dev/null
}

[ "$(id -u)" -eq 0 ] || die "请用 root 运行"
command -v docker >/dev/null 2>&1 || die "未找到 docker"
[ -f "$APP_DIR/config.env" ] || die "缺少 $APP_DIR/config.env；更新不会创建或覆盖真实配置"
STAGE=$(mktemp -d /tmp/parking-monitor-update.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT INT TERM
for file in $FILES; do fetch "$RAW_BASE/$file" "$STAGE/$file"; done
sh -n "$STAGE/install.sh"
sh -n "$STAGE/update.sh"
sh -n "$STAGE/parking-token"
docker run --rm -v "$STAGE/monitor.py:/tmp/monitor.py:ro" "$IMAGE" python -m py_compile /tmp/monitor.py

mkdir -p "$APP_DIR/data" "$APP_DIR/backups"
STAMP=$(date +%Y%m%d%H%M%S)
for file in $FILES; do
    [ -f "$APP_DIR/$file" ] && cp "$APP_DIR/$file" "$APP_DIR/backups/$file.$STAMP"
    cp "$STAGE/$file" "$APP_DIR/$file"
done
chmod 755 "$APP_DIR/install.sh" "$APP_DIR/update.sh" "$APP_DIR/parking-token"
chmod 644 "$APP_DIR/monitor.py" "$APP_DIR/config.env.example"
chmod 600 "$APP_DIR/config.env"
cp "$APP_DIR/parking-token" /usr/bin/parking-token
chmod 755 /usr/bin/parking-token

printf '%s\n' '脚本已更新；config.env 和 data/ 未改动。正在验证……'
restart
sleep 2
if docker exec "$CONTAINER" python /app/monitor.py --check; then
    printf '%s\n' '更新完成，停车 API 验证通过。'
    docker logs --tail 20 "$CONTAINER"
else
    printf '%s\n' '验证失败，正在回滚程序文件……' >&2
    for file in $FILES; do
        [ -f "$APP_DIR/backups/$file.$STAMP" ] && cp "$APP_DIR/backups/$file.$STAMP" "$APP_DIR/$file"
    done
    cp "$APP_DIR/parking-token" /usr/bin/parking-token 2>/dev/null || true
    restart
    die "更新未启用；旧版已恢复"
fi
