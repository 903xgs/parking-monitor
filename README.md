# parking-monitor

在 iStoreOS 上运行的车辆进出监控。程序定时查询停车接口，并在车辆入场或离场时通过 Bark 通知手机。

## 安全约定

- GitHub 只保存程序，不保存凭证、真实车牌和运行数据。
- 真实配置仅保存在 `/opt/parking-monitor/config.env`，文件权限为 `600`。
- `.gitignore` 已排除 `config.env`、`data/`、HAR、日志、备份和临时文件。
- 不要把浏览器 HAR、JWT、Bark Key 或真实 `config.env` 提交到仓库。

## 安装

在 iStoreOS SSH 中以 root 执行：

```sh
curl -fsSL https://raw.githubusercontent.com/903xgs/parking-monitor/main/install.sh | sh
```

首次执行会创建 `/opt/parking-monitor/config.env`。编辑它，填入 JWT、Bark Key、停车场编号和车辆，然后再次执行安装命令。真实配置格式如下，但值只能保存在 iStoreOS：

```env
ACCESS_TOKEN=
BARK_KEY=
PARKING_ID=
CARS=
CHECK_INTERVAL=60
```

部署方式保持为：

```text
容器名：parking-monitor
镜像：python:3.12-alpine
程序：/opt/parking-monitor/monitor.py -> /app/monitor.py（只读）
数据：/opt/parking-monitor/data -> /app/data
重启策略：unless-stopped
```

## 日常使用

拉取最新版并重建容器；本地 `config.env` 和 `data/` 不会被覆盖，验证失败会自动回滚：

```sh
curl -fsSL https://raw.githubusercontent.com/903xgs/parking-monitor/main/update.sh | sh
```

Token 过期后运行：

```sh
parking-token
```

该命令安装在 iStoreOS/OpenWrt 默认 PATH 中的 `/usr/bin/parking-token`。

该命令会隐藏输入，检查 JWT 三段格式，解析并显示 `expires_in` 或 `exp`，备份并更新 `config.env`，重建容器并调用停车 API 验证。验证失败时恢复原配置和容器。

查看日志：

```sh
docker logs --tail 50 parking-monitor
```

只验证停车 API，不发送 Bark：

```sh
docker exec parking-monitor python /app/monitor.py --check
```

## 配置

仓库内的 `config.env.example` 不含真实值。程序沿用当前已工作的 `queryTempFee` 接口、`PARKING_ID`、`CARS` 配置和连续两次确认离场逻辑。

从原有 iStoreOS 部署迁移时，不需要删除 Docker 或 `/opt/parking-monitor`。直接运行更新命令即可；更新脚本保留 `config.env` 和 `data/`，验证失败会恢复旧程序。不要上传原始压缩包、HAR、`config.env`、`state.json` 或 `monitor.py.bak`。
