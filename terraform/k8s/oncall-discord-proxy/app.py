import json
import os
import time
from datetime import datetime, timezone
from typing import Tuple

import redis
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

def clean_webhook(url: str) -> str:
    clean = (url or "").rstrip("/")
    if clean.endswith("/slack"):
        clean = clean[: -len("/slack")]
    return clean

def with_wait(url: str) -> str:
    if not url:
        return ""
    if "?" in url:
        if "wait=true" in url.split("?", 1)[1]:
            return url
        return f"{url}&wait=true"
    return f"{url}?wait=true"

DISCORD_CRITICAL = clean_webhook(os.environ.get("DISCORD_CRITICAL_URL", ""))
DISCORD_WARNING = clean_webhook(os.environ.get("DISCORD_WARNING_URL", ""))
ONCALL_ROLE_ID = os.environ.get("DISCORD_ONCALL_ROLE_ID", "").strip()

REDIS_HOST = os.environ.get("REDIS_HOST", "oncall-redis-master.monitoring-oncall.svc")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)

def choose_webhook(severity: str) -> str:
    sev = (severity or "").upper()
    if sev == "CRITICAL":
        return DISCORD_CRITICAL
    return DISCORD_WARNING

def parse_iso(value: str):
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None

def fmt_time(value: str) -> str:
    dt = parse_iso(value)
    if not dt:
        return "n/a"
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return str(int(dt.timestamp()))

def discord_ts(value: str, style: str) -> str:
    unix = fmt_time(value)
    if unix == "n/a":
        return "n/a"
    return f"<t:{unix}:{style}>"

def fmt_duration(start: str, end: str) -> str:
    start_dt = parse_iso(start)
    end_dt = parse_iso(end)
    if not start_dt or not end_dt:
        return "—"
    if not start_dt.tzinfo:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if not end_dt.tzinfo:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    seconds = int((end_dt - start_dt).total_seconds())
    if seconds < 0:
        seconds = -seconds
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)

def normalize(data: dict) -> dict:
    event = data.get("event") or {}
    event_type = ""
    event_time = ""
    if isinstance(event, dict):
        event_type = event.get("type") or event.get("TYPE") or ""
        event_time = event.get("time") or event.get("TIME") or ""
    elif isinstance(event, str):
        event_type = event

    alert_group = data.get("alert_group") if isinstance(data.get("alert_group"), dict) else {}
    alert_group_id = str(alert_group.get("id") or data.get("alert_group_id") or "")
    alert_group_title = str(alert_group.get("title") or data.get("alert_group_title") or "")
    alert_group_link = str((alert_group.get("permalinks") or {}).get("web") or data.get("alert_group_permalink") or "")
    alert_group_created_at = str(alert_group.get("created_at") or "")
    alert_group_resolved_at = str(alert_group.get("resolved_at") or "")
    alert_group_state = str(alert_group.get("state") or "")

    alert_payload = data.get("alert_payload") or {}
    if isinstance(alert_payload, str):
        try:
            alert_payload = json.loads(alert_payload) if alert_payload else {}
        except Exception:
            alert_payload = {}

    common_labels = {}
    common_annotations = {}
    if isinstance(alert_payload, dict):
        common_labels = alert_payload.get("commonLabels") or {}
        common_annotations = alert_payload.get("commonAnnotations") or {}
        if not common_labels:
            alerts = alert_payload.get("alerts") or []
            if alerts and isinstance(alerts[0], dict):
                common_labels = alerts[0].get("labels") or {}
                if not common_annotations:
                    common_annotations = alerts[0].get("annotations") or {}

    if not common_labels and isinstance(data.get("common_labels"), str):
        try:
            common_labels = json.loads(data.get("common_labels") or "{}")
        except Exception:
            common_labels = {}

    if not common_annotations and isinstance(data.get("common_annotations"), str):
        try:
            common_annotations = json.loads(data.get("common_annotations") or "{}")
        except Exception:
            common_annotations = {}

    return {
        "event_type": str(event_type or ""),
        "event_time": str(event_time or ""),
        "alert_group_id": alert_group_id,
        "alert_group_title": alert_group_title,
        "alert_group_link": alert_group_link,
        "alert_group_created_at": alert_group_created_at,
        "alert_group_resolved_at": alert_group_resolved_at,
        "alert_group_state": alert_group_state,
        "common_labels": common_labels if isinstance(common_labels, dict) else {},
        "common_annotations": common_annotations if isinstance(common_annotations, dict) else {},
    }

def pick_target(labels: dict) -> Tuple[str, str]:
    for key in ("node", "pod", "deployment", "daemonset", "statefulset", "job", "service", "ingress", "instance"):
        val = labels.get(key)
        if val:
            return key, str(val)
    return "", ""

def build_payload(data: dict) -> Tuple[str, str, str, dict]:
    ctx = normalize(data)

    event_type = ctx["event_type"].strip()
    event_key = event_type.lower()

    common_labels = ctx["common_labels"]
    common_annotations = ctx["common_annotations"]

    raw_severity = str(common_labels.get("severity") or "").strip()
    severity = raw_severity.upper() if raw_severity else "UNKNOWN"

    alertname = str(common_labels.get("alertname") or "").strip()
    summary = str(common_annotations.get("summary") or ctx["alert_group_title"] or alertname or "Alert").strip()
    description = str(common_annotations.get("description") or "").strip()

    cluster = str(common_labels.get("cluster") or "prod").strip() or "prod"
    namespace = str(common_labels.get("namespace") or "n/a").strip() or "n/a"
    target_key, target_val = pick_target(common_labels)
    target = f"{target_key}={target_val}" if target_key and target_val else "n/a"

    created_at = ctx["alert_group_created_at"] or (ctx["event_time"] if event_key == "alert group created" else "")
    resolved_at = ctx["alert_group_resolved_at"] or (ctx["event_time"] if event_key == "resolve" else "")
    is_resolved = bool(resolved_at) or event_key == "resolve" or ctx["alert_group_state"].lower() == "resolved"

    status_label = "✅ RESOLVED" if is_resolved else "🚨 FIRING"

    severity_emoji = {
        "CRITICAL": "🟥 ",
        "WARNING": "🟠 ",
        "INFO": "🟦 ",
    }.get(severity, "⚪ ")
    severity_value = (
        f"{severity_emoji}UNKNOWN (missing severity label)" if severity == "UNKNOWN" else f"{severity_emoji}{severity}"
    )

    event_label = "🆕 ALERT GROUP CREATED"

    triggered_abs = discord_ts(created_at, "f")
    triggered_rel = discord_ts(created_at, "R")
    if is_resolved:
        timeline = [f"🕒 Triggered: {triggered_abs}" if triggered_abs != "n/a" else "🕒 Triggered: n/a"]
    else:
        timeline = [
            f"🕒 Triggered: {triggered_abs} ({triggered_rel})" if triggered_abs != "n/a" else "🕒 Triggered: n/a"
        ]
    if is_resolved:
        resolved_abs = discord_ts(resolved_at, "f")
        resolved_rel = discord_ts(resolved_at, "R")
        timeline.append(
            f"✅ Resolved: {resolved_abs} ({resolved_rel})" if resolved_abs != "n/a" else "✅ Resolved: n/a"
        )
        duration = fmt_duration(created_at, resolved_at)
        timeline.append(f"⏱ Duration: {duration if duration != '—' else 'n/a'}")
    else:
        timeline.append("⏳ Resolved: pending")

    embed = {
        "title": summary,
        "url": ctx["alert_group_link"],
        "description": description[:1000] if description else None,
        "color": {
            "CRITICAL": 16731427,
            "WARNING": 16763904,
            "INFO": 3447003,
            "NONE": 10070709,
        }.get(severity, 10070709),
        "fields": [
            {"name": "Status", "value": status_label, "inline": True},
            {"name": "Severity", "value": severity_value, "inline": True},
            {"name": "Alert", "value": alertname or "n/a", "inline": True},
            {"name": "Event", "value": event_label, "inline": False},
            {"name": "Timeline", "value": "\n".join(timeline), "inline": False},
            {"name": "Where", "value": f"🧭 Cluster: {cluster}\n📦 Namespace: {namespace}\n🎯 Target: {target}", "inline": False},
        ],
    }
    if not embed["description"]:
        embed.pop("description", None)

    mention = f"<@&{ONCALL_ROLE_ID}>" if ONCALL_ROLE_ID else "@OnCall"
    mentions = {"parse": [], "roles": [ONCALL_ROLE_ID]} if ONCALL_ROLE_ID else {"parse": []}
    payload = {
        "content": "",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    if severity == "CRITICAL":
        payload["content"] = f"# {mention}"
        payload["allowed_mentions"] = mentions
    return ctx["alert_group_id"], event_key, severity, payload

def log(msg: str):
    print(msg, flush=True)

def send_discord(webhook: str, payload: dict, message_id: str = None) -> Tuple[int, dict]:
    if not webhook:
        return 400, {}
    headers = {"Content-Type": "application/json"}
    target = with_wait(webhook)
    method = requests.post
    if message_id:
        target = f"{webhook}/messages/{message_id}"
        method = requests.patch
    resp = method(target, headers=headers, data=json.dumps(payload), timeout=10)
    try:
        body = resp.json()
    except Exception:
        body = {}
    log(f"discord status={resp.status_code} message_id={body.get('id') if isinstance(body, dict) else ''}")
    return resp.status_code, body

def record_message(alert_group_id: str, message_id: str, ttl: int = 7 * 24 * 3600):
    if alert_group_id and message_id:
        redis_client.setex(f"oncall:discord:{alert_group_id}", ttl, message_id)

def read_message(alert_group_id: str) -> str:
    if not alert_group_id:
        return ""
    return redis_client.get(f"oncall:discord:{alert_group_id}") or ""

@app.route("/healthz")
def healthz():
    return "ok"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    alert_group_id, event_type, severity, payload = build_payload(data)
    target_webhook = choose_webhook(severity)
    log(f"recv event={event_type} group={alert_group_id} severity={severity}")
    if event_type == "resolve":
        msg_id = read_message(alert_group_id)
        if msg_id:
            status, body = send_discord(target_webhook, payload, message_id=msg_id)
            if status in (200, 204):
                return jsonify({"status": "patched", "message_id": msg_id}), 200
        status, body = send_discord(target_webhook, payload)
        if status == 200 and "id" in body:
            record_message(alert_group_id, body["id"])
        return jsonify({"status": "posted_resolve", "discord_status": status}), status

    status, body = send_discord(target_webhook, payload)
    if status == 200 and "id" in body:
        record_message(alert_group_id, body["id"])
        return jsonify({"status": "posted_create", "message_id": body["id"]}), 200
    return jsonify({"status": "error", "discord_status": status, "body": body}), status


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
