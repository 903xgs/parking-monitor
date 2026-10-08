#!/usr/bin/env python3

import argparse
import os
import sys
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

API_URL = "https://yaapay.hmzhtc.com/api/order/fee/queryTempFee"
BARK_SERVER = "https://api.day.app"

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "").strip()
BARK_KEY = os.getenv("BARK_KEY", "").strip()
PARKING_ID = os.getenv("PARKING_ID", "").strip()
CARS = [x.strip() for x in os.getenv("CARS", "").split(",") if x.strip()]
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

STATE_FILE = "/app/data/state.json"

CHINA_TZ = timezone(timedelta(hours=8))

CODE_INSIDE = "10000"
CODE_OUTSIDE = "120001"


def log(message):
    now = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

    temp_file = STATE_FILE + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, STATE_FILE)


def bark_push(title, body, group="停车监控"):
    if not BARK_KEY:
        log("BARK_KEY 未配置，无法发送通知")
        return False

    payload = {
        "device_key": BARK_KEY,
        "title": title,
        "body": body,
        "group": group,
        "sound": "bomb-has-been-planted-sound-effect-cs-go"
    }

    data = json.dumps(
        payload,
        ensure_ascii=False
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{BARK_SERVER}/push",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "ParkingMonitor/1.0"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()

        log(f"Bark 已发送：{title}")
        return True

    except Exception as e:
        log(f"Bark 推送失败：{e}")
        return False


def format_timestamp(timestamp_ms):
    try:
        timestamp_ms = int(timestamp_ms)

        return datetime.fromtimestamp(
            timestamp_ms / 1000,
            CHINA_TZ
        ).strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return "未知"


def format_duration(minutes):
    try:
        minutes = int(minutes)
    except Exception:
        return "未知"

    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days}天")

    if hours:
        parts.append(f"{hours}小时")

    parts.append(f"{mins}分钟")

    return "".join(parts)


def query_car(car_number):
    payload = {
        "direction": "",
        "parkingId": PARKING_ID,
        "isBoxSearch": 1,
        "carNumber": car_number
    }

    data = json.dumps(
        payload,
        ensure_ascii=False
    ).encode("utf-8")

    headers = {
        "access-token": ACCESS_TOKEN,
        "parkid-cce": PARKING_ID,
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://yaapay.hmzhtc.com",
        "Referer": "https://yaapay.hmzhtc.com/"
    }

    req = urllib.request.Request(
        API_URL,
        data=data,
        headers=headers,
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)

        code = str(result.get("code", ""))

        if code == CODE_INSIDE:
            return {
                "status": "inside",
                "content": result.get("content") or {}
            }

        if code == CODE_OUTSIDE:
            return {
                "status": "outside"
            }

        return {
            "status": "error",
            "error": f"未知接口返回 code={code}, message={result.get('message')}"
        }

    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {
                "status": "token_error",
                "error": "HTTP 401"
            }

        return {
            "status": "error",
            "error": f"HTTP {e.code}"
        }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


def process_car(car, state):
    result = query_car(car)
    status = result["status"]

    old = state.get(car)

    if status == "token_error":
        log(f"{car}：access-token 已失效")
        return "token_error"

    if status == "error":
        log(f"{car}：查询失败：{result.get('error')}")
        return "error"

    # 第一次发现车辆，只建立基线，不通知
    if old is None:
        if status == "inside":
            content = result["content"]

            state[car] = {
                "status": "inside",
                "updated_at": int(time.time()),
                "enterRecordId": str(content.get("enterRecordId", "")),
                "enterTime": str(content.get("enterTime", "")),
                "parkingName": content.get("parkingName", ""),
                "parkingTime": content.get("parkingTime", 0),
                "outside_confirmations": 0
            }

            log(
                f"{car} 初始状态：inside（不发送通知）"
            )

        else:
            state[car] = {
                "status": "outside",
                "updated_at": int(time.time()),
                "outside_confirmations": 0
            }

            log(
                f"{car} 初始状态：outside（不发送通知）"
            )

        save_state(state)
        return status

    old_status = old.get("status")

    # 当前在场
    if status == "inside":
        content = result["content"]

        enter_record_id = str(
            content.get("enterRecordId", "")
        )

        enter_time = str(
            content.get("enterTime", "")
        )

        parking_name = (
            content.get("parkingName")
            or "停车场"
        )

        parking_time = content.get(
            "parkingTime",
            0
        )

        # 任何一次可靠的在场结果，都清除离场候选计数
        old["outside_confirmations"] = 0

        # 原来不在场，现在在场 = 入场
        if old_status == "outside":
            bark_push(
                "🚗 车辆进入停车场",
                (
                    f"{car}\n"
                    f"{parking_name}\n"
                    f"入场时间：{format_timestamp(enter_time)}"
                )
            )

            log(
                f"{car}：检测到入场 | "
                f"{parking_name} | "
                f"{format_timestamp(enter_time)}"
            )

        # 原来就在场，但停车记录 ID 改变
        elif (
            old_status == "inside"
            and old.get("enterRecordId")
            and enter_record_id
            and old.get("enterRecordId") != enter_record_id
        ):
            bark_push(
                "🚗 检测到新的停车记录",
                (
                    f"{car}\n"
                    f"{parking_name}\n"
                    f"入场时间：{format_timestamp(enter_time)}"
                )
            )

            log(
                f"{car}：enterRecordId 已变化，"
                f"视为新的停车记录"
            )

        state[car] = {
            "status": "inside",
            "updated_at": int(time.time()),
            "enterRecordId": enter_record_id,
            "enterTime": enter_time,
            "parkingName": parking_name,
            "parkingTime": parking_time,
            "outside_confirmations": 0
        }

        save_state(state)

        log(
            f"{car}：在场 | "
            f"{parking_name} | "
            f"已停 {format_duration(parking_time)}"
        )

        return "inside"

    # 当前接口显示不在场
    if status == "outside":

        # 原本就在外面，无需通知
        if old_status == "outside":
            old["outside_confirmations"] = 0
            old["updated_at"] = int(time.time())

            state[car] = old
            save_state(state)

            log(f"{car}：不在场")
            return "outside"

        # 原本在场，需要连续两次确认不在场
        confirmations = int(
            old.get("outside_confirmations", 0)
        ) + 1

        if confirmations < 2:
            old["outside_confirmations"] = confirmations
            old["updated_at"] = int(time.time())

            state[car] = old
            save_state(state)

            log(
                f"{car}：离场候选 "
                f"{confirmations}/2，等待下一次确认"
            )

            return "outside_pending"

        parking_name = (
            old.get("parkingName")
            or "停车场"
        )

        enter_time = old.get(
            "enterTime",
            ""
        )

        bark_push(
            "🚙 车辆离开停车场",
            (
                f"{car}\n"
                f"{parking_name}\n"
                f"入场时间：{format_timestamp(enter_time)}\n"
                f"离场确认时间："
                f"{datetime.now(CHINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
            )
        )

        log(
            f"{car}：连续两次确认不在场，"
            f"检测到离场"
        )

        state[car] = {
            "status": "outside",
            "updated_at": int(time.time()),
            "outside_confirmations": 0
        }

        save_state(state)

        return "outside"


def check_once():
    if not ACCESS_TOKEN:
        log("ACCESS_TOKEN 未配置")
        return 1

    if not PARKING_ID:
        log("PARKING_ID 未配置")
        return 1

    if not CARS:
        log("CARS 未配置")
        return 1

    failed = False

    for car in CARS:
        result = query_car(car)
        status = result.get("status")

        if status in ("inside", "outside"):
            log(f"{car}：停车 API 验证通过，状态={status}")
        else:
            failed = True
            log(f"{car}：停车 API 验证失败：{result.get('error', status)}")

    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只验证停车 API，不发送通知")
    args = parser.parse_args()

    if args.check:
        return check_once()

    if not ACCESS_TOKEN:
        raise RuntimeError(
            "ACCESS_TOKEN 未配置"
        )

    if not PARKING_ID:
        raise RuntimeError(
            "PARKING_ID 未配置"
        )

    if not CARS:
        raise RuntimeError(
            "CARS 未配置"
        )

    log("=============================================")
    log("停车监控启动")
    log(f"停车场 ID：{PARKING_ID}")
    log(f"监控车辆：{', '.join(CARS)}")
    log(f"查询间隔：{CHECK_INTERVAL} 秒")
    log("=============================================")

    state = load_state()

    token_alerted = False

    while True:
        token_error_found = False

        for car in CARS:
            try:
                result = process_car(
                    car,
                    state
                )

                if result == "token_error":
                    token_error_found = True

            except Exception as e:
                log(
                    f"{car}：处理异常：{e}"
                )

        if token_error_found:
            if not token_alerted:
                bark_push(
                    "⚠️ 停车监控 Token 失效",
                    (
                        "红门智慧停车 access-token "
                        "已失效，需要更新 config.env "
                        "中的 ACCESS_TOKEN。"
                    )
                )

                token_alerted = True

        else:
            token_alerted = False

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
