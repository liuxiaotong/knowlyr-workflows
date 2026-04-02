#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASE_LABELS = {
    "install_dependencies": "安装依赖",
    "install_build_dependencies": "安装构建依赖",
    "build": "构建",
    "build_site": "构建站点",
    "prepare_bundle": "打包产物",
    "snapshot": "创建回滚快照",
    "stop_service": "停止旧服务",
    "pre_deploy_script": "执行发布前脚本",
    "deploy_code": "同步代码",
    "deploy_staging": "发布到预发布目录",
    "write_server_env": "写入服务器环境变量",
    "restart_services": "重启服务",
    "run_migrations": "执行数据库迁移",
    "post_deploy_script": "执行发布后脚本",
    "health_check": "上线后检查",
    "verify_build": "构建校验",
    "staging_health_check": "预发布校验",
    "atomic_switch": "原子切换",
    "restore_server_dirs": "恢复服务器托管目录",
    "cleanup": "清理备份",
    "completed": "完成",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deploy receipt artifacts.")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--status", required=True, choices=("success", "failure"))
    parser.add_argument("--subject", default="")
    parser.add_argument("--public-url", default="")
    parser.add_argument("--health-url", default="")
    parser.add_argument("--service-name", default="")
    parser.add_argument("--target-path", default="")
    parser.add_argument("--phase", default="")
    parser.add_argument("--rollback-attempted", default="false")
    parser.add_argument("--rollback-result", default="")
    parser.add_argument("--smoke-file", default="")
    parser.add_argument("--legacy-receipt", default="")
    return parser.parse_args()


def to_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_json(path: str) -> dict[str, Any]:
    if not path:
        return {}
    candidate = Path(path)
    if not candidate.exists():
        return {}
    return json.loads(candidate.read_text(encoding="utf-8"))


def phase_label(phase: str) -> str:
    return PHASE_LABELS.get(phase, phase or "未知阶段")


def smoke_summary(smoke: dict[str, Any], status: str, phase: str) -> str:
    if smoke.get("summary"):
        return str(smoke["summary"])
    if smoke.get("status") == "success":
        return "上线后检查通过"
    if smoke.get("status") == "failure":
        return "上线后检查失败"
    if status == "failure":
        return f"未完成，当前卡在“{phase_label(phase)}”"
    return "已完成，但未写入详细上线后检查摘要"


def rollback_summary(attempted: bool, result: str) -> str:
    if not attempted:
        if result == "available_if_needed":
            return "未触发，回滚路径仍可用"
        return "未触发"
    mapping = {
        "success": "已成功",
        "failed": "尝试了，但失败",
        "not_available": "尝试了，但缺少可用回滚快照",
        "partial": "已触发，结果需要人工复核",
    }
    return mapping.get(result, result or "已触发，结果待确认")


def repo_name(full_name: str) -> str:
    if "/" in full_name:
        return full_name.split("/", 1)[1]
    return full_name


def build_markdown(
    *,
    status: str,
    subject: str,
    public_url: str,
    run_url: str,
    health_url: str,
    service_name: str,
    target_path: str,
    commit_short: str,
    phase: str,
    smoke_summary_text: str,
    rollback_text: str,
) -> str:
    title = "✅ {} 部署成功，已经上线" if status == "success" else "❌ {} 这次没发成，线上可能还是旧版本"
    lines = [
        "## 墨言回执",
        title.format(subject),
        "",
        "你现在只需要知道：",
    ]
    if status == "success":
        lines.append(f"- {smoke_summary_text}")
        lines.append(f"- 回滚路径：{rollback_text}")
        if public_url:
            lines.append(f"- 产品地址：{public_url}")
        lines.append(f"- 版本：{commit_short}")
    else:
        lines.append(f"- 卡在：{phase_label(phase)}")
        lines.append(f"- 上线后检查：{smoke_summary_text}")
        lines.append(f"- 自动回滚：{rollback_text}")
        if public_url:
            lines.append(f"- 对外地址暂时还是原来的：{public_url}")
        lines.append(f"- 计划发布版本：{commit_short}")

    lines.extend(["", "排查时再看：", f"- 发布记录：{run_url}"])
    if health_url:
        lines.append(f"- 检查入口：{health_url}")
    if service_name:
        lines.append(f"- 服务名：{service_name}")
    if target_path:
        lines.append(f"- 目标路径：{target_path}")
    return "\n".join(lines).strip() + "\n"


def build_plain_text(markdown: str) -> str:
    text = markdown.replace("## ", "").replace("**", "")
    return text


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    smoke = load_json(args.smoke_file)

    github_repository = os.getenv("GITHUB_REPOSITORY", "")
    github_sha = os.getenv("GITHUB_SHA", "")
    commit_short = github_sha[:7] if github_sha else ""
    server_url = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_url = f"{server_url}/{github_repository}/actions/runs/{run_id}" if github_repository and run_id else ""
    commit_url = f"{server_url}/{github_repository}/commit/{github_sha}" if github_repository and github_sha else ""
    subject = args.subject or repo_name(github_repository or "deploy")

    rollback_attempted = to_bool(args.rollback_attempted)
    rollback_result = args.rollback_result.strip()
    smoke_summary_text = smoke_summary(smoke, args.status, args.phase)
    rollback_text = rollback_summary(rollback_attempted, rollback_result)
    markdown = build_markdown(
        status=args.status,
        subject=subject,
        public_url=args.public_url.strip(),
        run_url=run_url,
        health_url=args.health_url.strip(),
        service_name=args.service_name.strip(),
        target_path=args.target_path.strip(),
        commit_short=commit_short or "unknown",
        phase=args.phase.strip(),
        smoke_summary_text=smoke_summary_text,
        rollback_text=rollback_text,
    )
    plain_text = build_plain_text(markdown)

    payload = {
        "status": args.status,
        "repo": github_repository,
        "repo_name": repo_name(github_repository or ""),
        "subject": subject,
        "sha": github_sha,
        "commit_short": commit_short,
        "commit_url": commit_url,
        "run_url": run_url,
        "event_name": os.getenv("GITHUB_EVENT_NAME", ""),
        "ref_name": os.getenv("GITHUB_REF_NAME", ""),
        "public_url": args.public_url.strip() or None,
        "phase": args.phase.strip(),
        "phase_label": phase_label(args.phase.strip()),
        "rollback_attempted": rollback_attempted,
        "rollback_result": rollback_result or None,
        "rollback_summary": rollback_text,
        "smoke_summary": smoke_summary_text,
        "release_smoke": smoke or None,
        "summary_markdown": markdown,
        "receipt_text": plain_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    (output_dir / "deploy-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "deploy-report.md").write_text(markdown, encoding="utf-8")

    if args.legacy_receipt:
        Path(args.legacy_receipt).write_text(plain_text, encoding="utf-8")


if __name__ == "__main__":
    main()
