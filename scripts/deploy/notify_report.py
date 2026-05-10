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


ANTGATHER_CONTENT_TYPE = "text"
ANTGATHER_SUB_TYPE = "assistant_receipt"
ANTGATHER_CARD_TYPE = "receipt"


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


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _release_smoke(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("release_smoke")
    return value if isinstance(value, dict) else {}


def _subject(report: dict[str, Any]) -> str:
    return _text(report.get("subject") or report.get("repo_name"), "部署")


def _commit_short(report: dict[str, Any]) -> str:
    return _text(report.get("commit_short") or _text(report.get("sha"))[:7], "unknown")


def _is_success(report: dict[str, Any]) -> bool:
    return _text(report.get("status")).lower() == "success"


def _title(report: dict[str, Any]) -> str:
    subject = _subject(report)
    return f"✅ {subject} 部署完成" if _is_success(report) else f"❌ {subject} 部署失败"


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


def build_feishu_card(report: dict[str, Any]) -> dict[str, Any]:
    """Build a Feishu-native interactive deploy receipt card."""
    smoke = _release_smoke(report)
    success = _is_success(report)
    release_note = _text(report.get("release_note"), "本次提交已上线")
    observed_status = _text(smoke.get("observed_status"))
    smoke_summary = _text(report.get("smoke_summary") or smoke.get("summary"), "未记录")
    rollback_summary = _text(report.get("rollback_summary") or report.get("rollback_result"), "未触发")
    phase = _text(report.get("phase_label") or report.get("phase"), "unknown")
    run_url = _text(report.get("run_url"))
    commit_url = _text(report.get("commit_url"))
    public_url = _text(report.get("public_url"))
    health_url = _text(report.get("health_url"))
    service_name = _text(report.get("service_name") or report.get("service"))
    target_path = _text(report.get("target_path"))

    lines = [
        f"**升级说明**：{release_note}",
        f"**{'Health' if success else '失败阶段'}**：{observed_status or '通过' if success else phase}",
        f"**Smoke**：{smoke_summary}",
        f"**Commit**：{_commit_short(report)} / `{_text(report.get('ref_name'), 'main')}`",
    ]
    if not success:
        lines.append(f"**Rollback**：{rollback_summary}")
    if public_url:
        lines.append(f"**产品地址**：{public_url}")
    if health_url:
        lines.append(f"**检查入口**：{health_url}")
    if service_name:
        lines.append(f"**服务名**：{service_name}")
    if target_path:
        lines.append(f"**目标路径**：{target_path}")

    actions: list[dict[str, Any]] = []
    if run_url:
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看发布记录"},
                "type": "primary" if success else "danger",
                "url": run_url,
            }
        )
    if commit_url:
        actions.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "查看提交"},
                "type": "default",
                "url": commit_url,
            }
        )

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
    ]
    if actions:
        elements.extend([{"tag": "hr"}, {"tag": "action", "actions": actions}])

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "green" if success else "red",
            "title": {"tag": "plain_text", "content": _title(report)},
        },
        "elements": elements,
    }


def _receipt_item(label: str, text: Any, href: str = "") -> dict[str, str]:
    item = {"label": label, "text": _text(text, "未记录")}
    if href:
        item["href"] = href
    return item


def build_antgather_receipt_card(report: dict[str, Any]) -> dict[str, Any]:
    """Build an AntGather-native assistant receipt card payload."""
    smoke = _release_smoke(report)
    success = _is_success(report)
    release_note = _text(report.get("release_note"), "本次提交已上线")
    observed_status = _text(smoke.get("observed_status"), "通过")
    phase = _text(report.get("phase_label") or report.get("phase"), "unknown")
    smoke_summary = _text(report.get("smoke_summary") or smoke.get("summary"), "未记录")
    rollback_summary = _text(report.get("rollback_summary") or report.get("rollback_result"), "未触发")
    run_url = _text(report.get("run_url"))
    commit_url = _text(report.get("commit_url"))
    public_url = _text(report.get("public_url"))
    health_url = _text(report.get("health_url"))
    service_name = _text(report.get("service_name") or report.get("service") or _subject(report))
    target_path = _text(report.get("target_path"))
    commit_short = _commit_short(report)

    title = (
        f"{_subject(report)} 部署成功，已经上线"
        if success
        else f"{_subject(report)} 部署失败，需要继续排查"
    )
    health_or_phase = (
        _receipt_item("上线后检查", observed_status)
        if success
        else _receipt_item("失败阶段", phase)
    )

    sections = [
        {
            "title": "你现在只需要知道",
            "items": [
                _receipt_item("升级说明", release_note),
                health_or_phase,
                _receipt_item("Smoke", smoke_summary),
                _receipt_item("回滚路径", rollback_summary),
            ],
        },
        {
            "title": "排查时再看",
            "items": [
                _receipt_item("提交", commit_short, commit_url),
                _receipt_item("发布记录", run_url, run_url),
            ],
        },
    ]
    if public_url:
        sections[0]["items"].append(_receipt_item("产品地址", public_url, public_url))
    if health_url:
        sections[1]["items"].append(_receipt_item("检查入口", health_url, health_url))
    if service_name:
        sections[1]["items"].append(_receipt_item("服务名", service_name))
    if target_path:
        sections[1]["items"].append(_receipt_item("目标路径", target_path))

    return {
        "actor": "墨言",
        "title": title,
        "subtitle": f"部署回执 · {_subject(report)}",
        "tone": "success" if success else "danger",
        "sections": sections,
        "primary_label": "查看发布记录" if run_url else "查看详情",
        "primary_url": run_url,
        "version": commit_short,
        "service": service_name,
        "target_path": target_path,
        "product_url": public_url,
    }


def build_antgather_content(report: dict[str, Any]) -> str:
    # AntGather renders the native card from card_type/card_data; content is only
    # a non-empty fallback for clients that have not learned receipt cards yet.
    return "[receipt 卡片]"


def send_feishu(report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not env_bool("DEPLOY_NOTIFY_FEISHU"):
        return status_payload("feishu", "skipped", reason="disabled")

    text = report_text(report, "feishu_text")
    if not text:
        return status_payload("feishu", "skipped", reason="missing_message_text")
    card = build_feishu_card(report)

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
            msg_type="interactive",
        )

    if dry_run:
        return status_payload(
            "feishu",
            "accepted",
            dry_run=True,
            receive_id_type=receive_id_type,
            msg_type="interactive",
            card_title=card.get("header", {}).get("title", {}).get("content"),
            fallback_message_chars=len(text),
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
            msg_type="interactive",
        )

    send_status, send_payload, send_text = http_json(
        f"{api_base}/im/v1/messages?receive_id_type={receive_id_type}",
        payload={
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    ok = 200 <= send_status < 300 and int((send_payload or {}).get("code", 0) or 0) == 0
    result = status_payload(
        "feishu",
        "accepted" if ok else "failed",
        http_status=send_status or None,
        receive_id_type=receive_id_type,
        msg_type="interactive",
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


def _native_card_accepted(response_payload: Any) -> bool:
    return isinstance(response_payload, dict) and response_payload.get("card_type") == ANTGATHER_CARD_TYPE


def send_antgather(report: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    if not env_bool("DEPLOY_NOTIFY_ANTGATHER"):
        return status_payload("antgather", "skipped", reason="disabled")

    content = build_antgather_content(report)
    card_data = build_antgather_receipt_card(report)
    if not content or not card_data:
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
            content_type=ANTGATHER_CONTENT_TYPE,
            sub_type=ANTGATHER_SUB_TYPE,
            card_type=ANTGATHER_CARD_TYPE,
        )

    payload = {
        "sender_id": sender_id,
        "recipient_id": recipient_id,
        "content": content,
        "msg_type": "private",
        "sub_type": ANTGATHER_SUB_TYPE,
        "content_type": ANTGATHER_CONTENT_TYPE,
        "card_type": ANTGATHER_CARD_TYPE,
        "card_data": card_data,
        "card_status": "answered",
    }

    if dry_run:
        return status_payload(
            "antgather",
            "accepted",
            dry_run=True,
            sender_id=sender_id,
            recipient_id=recipient_id,
            content_type=ANTGATHER_CONTENT_TYPE,
            sub_type=ANTGATHER_SUB_TYPE,
            card_type=ANTGATHER_CARD_TYPE,
            native_card=True,
            card_title=card_data.get("title"),
            message_chars=len(content),
        )

    http_status, response_payload, response_text = http_json(
        f"{api_url}/api/internal/messages",
        payload=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    ok = 200 <= http_status < 300
    native_card = _native_card_accepted(response_payload)
    result = status_payload(
        "antgather",
        "accepted" if ok else "failed",
        http_status=http_status or None,
        sender_id=sender_id,
        recipient_id=recipient_id,
        content_type=ANTGATHER_CONTENT_TYPE,
        sub_type=ANTGATHER_SUB_TYPE,
        card_type=ANTGATHER_CARD_TYPE,
        native_card=native_card,
        message_id=extract_message_id(response_payload) or None,
    )
    if ok and not native_card:
        result["warning"] = "antgather_response_missing_native_card_fields"
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
