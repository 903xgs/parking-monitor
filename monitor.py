#!/usr/bin/env python3
"""Parking entry/exit monitor; all private values come from config.env."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
STATE_FILE = DATA_DIR / "state.json"


def env(name, default=""):
    return os.getenv(name, default).strip()


def log(message):
    print(f"{datetime.now().astimezone():%Y-%m-%d %H:%M:%S%z} {message}", flush=True)


def get_path(value, path):
    for part in path.split(".") if path else []:
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def vehicles():
    raw = env("VEHICLES_JSON")
    if raw:
        items = json.loads(raw)
        if not isinstance(items, list):
            raise ValueError("VEHICLES_JSON must be a JSON array")
        result = []
        for item in items:
            if isinstance(item, str):
                result.append({"plate": item, "name": item})
            elif isinstance(item, dict) and item.get("plate"):
                result.append({"plate": str(item["plate"]), "name": str(item.get("name") or item["plate"])})
        return result
    return [{"plate": x.strip(), "name": x.strip()} for x in env("VEHICLES").split(",") if x.strip()]


def call_api(plate):
    url, token = env("API_URL"), env("ACCESS_TOKEN")
    if not url or not token:
        raise RuntimeError("API_URL or ACCESS_TOKEN is missing")
    body = env("API_BODY_TEMPLATE", '{"carNo":"{plate}"}').replace("{plate}", plate).encode("utf-8")
    headers = {
        "Content-Type": env("API_CONTENT_TYPE", "application/json;charset=UTF-8"),
        env("ACCESS_TOKEN_HEADER", "access-token"): token,
        "User-Agent": env("API_USER_AGENT", "parking-monitor/1.0"),
    }
    if env("API_HEADERS_JSON"):
        headers.update({str(k): str(v) for k, v in json.loads(env("API_HEADERS_JSON")).items()})
    method = env("API_METHOD", "POST").upper()
    request = urllib.request.Request(url, None if method == "GET" else body, headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=float(env("API_TIMEOUT", "20"))) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"API did not return JSON: {raw[:300]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("API JSON root is not an object")
    code_path = env("SUCCESS_CODE_JSON_PATH")
    if code_path:
        actual = str(get_path(payload, code_path)).lower()
        allowed = {x.strip().lower() for x in env("SUCCESS_CODE_VALUES", "0,200,success").split(",")}
        if actual not in allowed:
            raise RuntimeError(f"API business status is {actual}: {str(payload)[:300]}")
    return payload


def parked(payload):
    configured = env("PARKED_JSON_PATH")
    candidates = [configured] if configured else [
        "data.isParked", "data.inPark", "data.parked", "data.status",
        "isParked", "inPark", "parked", "status",
    ]
    found = False
    for path in candidates:
        if not path:
            continue
        try:
            value, found = get_path(payload, path), True
            break
        except KeyError:
            pass
    if not found and env("PARKED_WHEN_DATA_NONEMPTY", "0") == "1":
        value, found = bool(payload.get("data")), True
    if not found:
        raise RuntimeError("cannot locate parking status; set PARKED_JSON_PATH")
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    yes = {x.strip().lower() for x in env("PARKED_TRUE_VALUES", "1,true,yes,in,parked,在场,停车中").split(",")}
    no = {x.strip().lower() for x in env("PARKED_FALSE_VALUES", "0,false,no,out,left,离场,未停车").split(",")}
    if normalized in yes:
        return True
    if normalized in no:
        return False
    raise RuntimeError(f"unknown parking status value: {value!r}")


def response_details(payload):
    output = []
    for label, paths in (
        ("停车场", ["data.parkName", "data.parkingName"]),
        ("入场", ["data.inTime", "data.entryTime"]),
        ("费用", ["data.fee", "data.amount", "data.tempFee"]),
    ):
        for path in paths:
            try:
                value = get_path(payload, path)
                if value not in (None, ""):
                    output.append(f"{label}：{value}")
                    break
            except KeyError:
                pass
    return "\n".join(output)


def bark(title, message):
    key = env("BARK_KEY")
    if not key:
        raise RuntimeError("BARK_KEY is missing")
    base = env("BARK_SERVER", "https://api.day.app").rstrip("/")
    url = "/".join([base, urllib.parse.quote(key, safe=""), urllib.parse.quote(title, safe=""), urllib.parse.quote(message, safe="")])
    query = urllib.parse.urlencode({"group": env("BARK_GROUP", "车辆进出提醒")})
    with urllib.request.urlopen(f"{url}?{query}", timeout=15) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Bark HTTP {response.status}")


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def check_once(notify):
    configured = vehicles()
    if not configured:
        raise RuntimeError("no vehicles configured")
    state, errors = load_state(), 0
    for vehicle in configured:
        plate, name = vehicle["plate"], vehicle["name"]
        try:
            payload = call_api(plate)
            current = parked(payload)
            previous = state.get(plate, {}).get("parked")
            state[plate] = {"parked": current, "checked_at": int(time.time())}
            log(f"API OK: {name} status={'parked' if current else 'away'}")
            if notify and previous is not None and bool(previous) != current:
                action = "车辆入场" if current else "车辆离场"
                bark(action, f"{name}\n{response_details(payload)}".rstrip())
        except Exception as exc:
            errors += 1
            log(f"ERROR: {name}: {exc}")
    save_state(state)
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="query once without Bark")
    args = parser.parse_args()
    try:
        if args.check:
            return 1 if check_once(False) else 0
        interval = max(15, int(env("POLL_INTERVAL", "60")))
        log(f"started; polling every {interval}s")
        while True:
            check_once(True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        log(f"FATAL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
