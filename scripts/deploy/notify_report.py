#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send deploy report notifications.")
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call external APIs; write accepted delivery records instead.",
    )
    return parser.parse_args()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_report(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def strip_quotes(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def read_env_file(path: str) -> dict[str, str]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    result: dict[str, str] = {}
    for line in candidate.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            result[key] = strip_quotes(value)
    return result


def read_feishu_bot_config(path: str, bot_id: str) -> dict[str, str]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}

    bots: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            if current:
                bots.append(current)
            current = {}
            line = line[2:].strip()
            if ":" not in line:
                continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.strip()] = strip_quotes(value)
    if current:
        bots.append(current)

    for item in bots:
        if item.get("bot_id") == bot_id:
            return item
    for item in bots:
        if item.get("primary", "").lower() == "true":
            return item
    return bots[0] if bots else {}


def http_json(
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = 12,
) -> tuple[int, dict[str, Any] | None, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(text) if text else None
            return int(response.status), parsed, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        parsed = None
        try:
            parsed = json.loads(text) if text else None
        except Exception:
            parsed = None
        return int(exc.code), parsed, text
    except Exception as exc:  # noqa: BLE001 - notification must be non-fatal.
        return 0, None, repr(exc)


def status_payload(channel: str, status: str, **extra: Any) -> dict[str, Any]:
    return {
        "channel": channel,
        "status": status,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def report_text(report: dict[str, Any], preferred_key: str) -> str:
    preferred = str(report.get(preferred_key) or "").strip()
    if preferred:
        return preferred
    if preferred_key == "feishu_text":
        return default_feishu_text(report)
    return str(
        report.get("receipt_text")
        or report.get("summary_markdown")
        or ""
    ).strip()


def default_feishu_text(report: dict[str, Any]) -> str:
    status = str(report.get("status") or "").strip().lower()
    subject = str(report.get("subject") or report.get("repo_name") or "部署").strip()
    commit_short = str(report.get("commit_short") or str(report.get("sha") or "")[:7] or "unknown").strip()
    ref_name = str(report.get("ref_name") or "main").strip()
    run_url = str(report.get("run_url") or "").strip()
    public_url = str(report.get("public_url") or "").strip()
    smoke_summary = str(report.get("smoke_summary") or "").strip()
    release_note = str(report.get("release_note") or "").strip()
    phase = str(report.get("phase") or "unknown").strip()
    rollback_summary = str(report.get("rollback_summary") or report.get("rollback_result") or "").strip()

    if status == "success":
        lines = [
            f"✅ {subject} 部署完成",
        ]
        if release_note:
            lines.append(f"升级说明：{release_note}")
        lines.extend([f"commit: {commit_short}", f"ref: {ref_name}"])
        if run_url:
            lines.append(f"run: {run_url}")
        if public_url:
            lines.append(f"public: {public_url}")
        if smoke_summary:
            lines.append(f"smoke: {smoke_summary}")
        return "\n".join(lines)

    lines = [
        f"❌ {subject} 部署失败",
    ]
    if release_note:
        lines.append(f"计划升级：{release_note}")
    lines.extend([f"phase: {phase}", f"commit: {commit_short}", f"ref: {ref_name}"])
    if run_url:
        lines.append(f"run: {run_url}")
    if public_url:
        lines.append(f"public: {public_url}")
    if rollback_summary:
        lines.append(f"rollback: {rollback_summary}")
    return "\n".join(lines)


def send_feishu(report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not env_bool("DEPLOY_NOTIFY_FEISHU"):
        return status_payload("feishu", "skipped", reason="disabled")

    text = report_text(report, "feishu_text")
    if not text:
        return status_payload("feishu", "skipped", reason="missing_message_text")

    config = read_feishu_bot_config(
        os.getenv("FEISHU_CONFIG_PATH", "/opt/knowlyr-silt/project/.silt/feishu.yaml"),
        os.getenv("FEISHU_BOT_ID", "moyan"),
    )
    app_id = os.getenv("FEISHU_APP_ID") or os.getenv("FEISHU_MOYAN_APP_ID") or config.get("app_id", "")
    app_secret = (
        os.getenv("FEISHU_APP_SECRET")
        or os.getenv("FEISHU_MOYAN_APP_SECRET")
        or config.get("app_secret", "")
    )
    receive_id = (
        os.getenv("FEISHU_RECEIVE_ID")
        or os.getenv("FEISHU_MOYAN_OWNER_OPEN_ID")
        or config.get("owner_open_id", "")
    )
    receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id")
    api_base = os.getenv("FEISHU_API_BASE", "https://open.feishu.cn/open-apis").rstrip("/")

    if not app_id or not app_secret or not receive_id:
        return status_payload(
            "feishu",
            "skipped",
            reason="missing_feishu_config",
            config_path=os.getenv("FEISHU_CONFIG_PATH", "/opt/knowlyr-silt/project/.silt/feishu.yaml"),
            has_app_id=bool(app_id),
            has_app_secret=bool(app_secret),
            has_receive_id=bool(receive_id),
        )

    if dry_run:
        return status_payload(
            "feishu",
            "accepted",
            dry_run=True,
            receive_id_type=receive_id_type,
            message_chars=len(text),
        )

    token_status, token_payload, token_text = http_json(
        f"{api_base}/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
    )
    token = str((token_payload or {}).get("tenant_access_token") or "")
    if not token:
        return status_payload(
            "feishu",
            "failed",
            error="tenant_access_token_missing",
            http_status=token_status or None,
            response_excerpt=" ".join(token_text.split())[:240],
        )

    content = json.dumps({"text": text}, ensure_ascii=False)
    send_status, send_payload, send_text = http_json(
        f"{api_base}/im/v1/messages?receive_id_type={receive_id_type}",
        payload={"receive_id": receive_id, "msg_type": "text", "content": content},
        headers={"Authorization": f"Bearer {token}"},
    )
    ok = 200 <= send_status < 300 and int((send_payload or {}).get("code", 0) or 0) == 0
    result = status_payload(
        "feishu",
        "accepted" if ok else "failed",
        http_status=send_status or None,
        receive_id_type=receive_id_type,
        message_id=((send_payload or {}).get("data") or {}).get("message_id"),
    )
    if not ok:
        result["error"] = f"HTTP {send_status}" if send_status else "request_failed"
        result["response_excerpt"] = " ".join(send_text.split())[:240]
    return result


def extract_message_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("message_id", "msg_id", "id"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for key in ("data", "message"):
            nested = extract_message_id(payload.get(key))
            if nested:
                return nested
    return ""


def send_antgather(report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not env_bool("DEPLOY_NOTIFY_ANTGATHER"):
        return status_payload("antgather", "skipped", reason="disabled")

    text = report_text(report, "antgather_dm_text")
    if not text:
        return status_payload("antgather", "skipped", reason="missing_message_text")

    env_file = read_env_file(os.getenv("ANTGATHER_ENV_PATH", "/var/www/antgather-api/.env"))
    token = (
        os.getenv("ANTGATHER_INTERNAL_TOKEN")
        or env_file.get("ANTGATHER_INTERNAL_TOKEN")
        or env_file.get("INTERNAL_SERVICE_TOKEN")
        or ""
    )
    api_url = os.getenv("ANTGATHER_NOTIFY_URL", "http://127.0.0.1:8200").rstrip("/")
    sender_id = os.getenv("ANTGATHER_NOTIFY_SENDER_ID", "AI3073")
    recipient_id = os.getenv("ANTGATHER_NOTIFY_RECIPIENT_ID", "1")

    if not token:
        return status_payload(
            "antgather",
            "skipped",
            reason="missing_antgather_token",
            env_path=os.getenv("ANTGATHER_ENV_PATH", "/var/www/antgather-api/.env"),
        )

    if dry_run:
        return status_payload(
            "antgather",
            "accepted",
            dry_run=True,
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_chars=len(text),
        )

    payload = {
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "content": text,
        "msg_type": "private",
        "content_type": "text",
    }
    http_status, response_payload, response_text = http_json(
        f"{api_url}/api/internal/messages",
        payload=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    ok = 200 <= http_status < 300
    result = status_payload(
        "antgather",
        "accepted" if ok else "failed",
        http_status=http_status or None,
        sender_id=sender_id,
        recipient_id=recipient_id,
        message_id=extract_message_id(response_payload) or None,
    )
    if not ok:
        result["error"] = f"HTTP {http_status}" if http_status else "request_failed"
        result["response_excerpt"] = " ".join(response_text.split())[:240]
    return result


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = read_report(args.report_json)

    results = {
        "status": "completed",
        "repo": report.get("repo"),
        "sha": report.get("sha"),
        "run_url": report.get("run_url"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channels": {
            "feishu": send_feishu(report, dry_run=args.dry_run),
            "antgather": send_antgather(report, dry_run=args.dry_run),
        },
    }

    write_json(output_dir / "feishu-notification.json", results["channels"]["feishu"])
    write_json(output_dir / "antgather-notification.json", results["channels"]["antgather"])
    write_json(output_dir / "deploy-notifications.json", results)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
