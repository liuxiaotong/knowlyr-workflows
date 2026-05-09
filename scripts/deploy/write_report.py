#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
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

MAX_RELEASE_NOTE_CHARS = 120
MAX_RELEASE_DIFF_CHARS = 12000


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


def git_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def clean_subject(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(
        r"^(feat|fix|docs|refactor|perf|test|chore|ci|build|style|revert)(\([^)]+\))?!?:\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    if re.match(r"^Merge pull request #\d+ from .+$", text, flags=re.IGNORECASE):
        return ""
    if re.match(r"^Merge branch .+$", text, flags=re.IGNORECASE):
        return ""
    return text.strip("。.;； ")


def clean_release_note(raw: str) -> str:
    text = re.sub(r"\s+", " ", raw).strip()
    text = re.sub(
        r"^(release[-_ ]?note|upgrade[-_ ]?note|升级说明|变更说明|版本变化)\s*[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.strip(" -•*。.;；'\"")
    if len(text) > MAX_RELEASE_NOTE_CHARS:
        text = text[: MAX_RELEASE_NOTE_CHARS - 3].rstrip(" ，,。.;；") + "..."
    return text


def weak_release_note(text: str) -> bool:
    lower = text.lower().strip(" 。.;；")
    weak = {
        "本次提交已上线",
        "本次更新已上线",
        "更新了一些内容",
        "修复若干问题",
        "优化若干体验",
        "update",
        "updates",
        "misc",
    }
    return not text or lower in weak or len(text) < 8


def release_range() -> str:
    before = os.getenv("GITHUB_EVENT_BEFORE", "").strip()
    sha = os.getenv("GITHUB_SHA", "").strip()

    event_path = os.getenv("GITHUB_EVENT_PATH", "").strip()
    if not before and event_path and Path(event_path).exists():
        try:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            before = str(event.get("before") or "").strip()
        except Exception:
            before = ""

    if before and not re.fullmatch(r"0+", before) and sha:
        return f"{before}..{sha}"
    if sha:
        parent = git_output(["rev-parse", f"{sha}^"]).strip()
        if parent:
            return f"{parent}..{sha}"
    return "HEAD~1..HEAD"


def release_notes_from_bodies(range_expr: str) -> list[str]:
    bodies = git_output(["log", "--format=%B%x1e", range_expr]).split("\x1e")
    notes: list[str] = []
    seen: set[str] = set()
    for body in bodies:
        for line in body.splitlines():
            match = re.match(
                r"^(?:release[-_ ]?note|upgrade[-_ ]?note|升级说明|变更说明)\s*[:：]\s*(.+)$",
                line.strip(),
                flags=re.IGNORECASE,
            )
            if not match:
                continue
            item = clean_release_note(match.group(1))
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            notes.append(item)
    return notes


def fallback_note_from_subjects(range_expr: str) -> str:
    subjects = git_output(["log", "--format=%s", range_expr]).splitlines()
    if not subjects:
        subjects = git_output(["log", "--format=%s", "-n", "5", os.getenv("GITHUB_SHA") or "HEAD"]).splitlines()
    cleaned: list[str] = []
    seen: set[str] = set()
    for subject in subjects:
        item = clean_subject(subject)
        lower = item.lower()
        if not item or lower.startswith("merge ") or lower in {"update", "updates", "misc", "wip"}:
            continue
        if lower in seen:
            continue
        seen.add(lower)
        cleaned.append(item)
    return clean_release_note("；".join(cleaned[:3])) if cleaned else "本次提交已上线"


def release_context(range_expr: str) -> str:
    commits = git_output(["log", "--format=%h %s", range_expr]).strip()
    names = git_output(["diff", "--name-status", range_expr]).strip()
    stat = git_output(["diff", "--stat", range_expr]).strip()
    diff = git_output(["diff", "--unified=1", "--no-ext-diff", range_expr]).strip()
    if len(diff) > MAX_RELEASE_DIFF_CHARS:
        diff = diff[:MAX_RELEASE_DIFF_CHARS] + "\n...[diff truncated]"
    return "\n\n".join(
        [
            "Commits:\n" + (commits or "(none)"),
            "Changed files:\n" + (names or "(none)"),
            "Diff stat:\n" + (stat or "(none)"),
            "Diff:\n" + (diff or "(none)"),
        ]
    )


def llm_release_note(context: str, subject: str) -> str:
    api_key = (
        os.getenv("RELEASE_NOTE_LLM_API_KEY", "").strip()
        or os.getenv("SENTINEL_CODEX_API_KEY", "").strip()
        or os.getenv("SENTINEL_SILT_OPENAI_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("LLM_PROXY_API_KEY", "").strip()
    )
    if not api_key:
        return ""
    base_url = os.getenv("RELEASE_NOTE_LLM_BASE_URL", "https://sentinel.knowlyr.com/v1").strip().rstrip("/")
    model = os.getenv("RELEASE_NOTE_LLM_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是发布经理。根据代码 diff 判断本次版本变化，写一句中文升级说明。"
                    "要求：只输出一句；40-90 字；不要自称；不要写“AI”；不要写空泛词；"
                    "不要说部署成功、健康检查、commit；必须说清这个版本具体实现或改变了什么。"
                ),
            },
            {"role": "user", "content": f"项目：{subject or '当前项目'}\n请生成通知里的“升级说明”。\n\n{context}"},
        ],
        "max_tokens": 120,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001
        print(f"LLM release note skipped: {exc}")
        return ""
    choices = result.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return clean_release_note(str(message.get("content") or choices[0].get("text") or ""))


def build_release_note(subject: str) -> tuple[str, str]:
    manual = (
        os.getenv("DEPLOY_RELEASE_NOTE", "").strip()
        or os.getenv("RELEASE_NOTE", "").strip()
        or os.getenv("MANUAL_RELEASE_NOTE", "").strip()
    )
    if manual:
        return clean_release_note(manual), "manual"

    range_expr = release_range()
    note = llm_release_note(release_context(range_expr), subject)
    if not weak_release_note(note):
        return note, "llm"

    explicit_notes = release_notes_from_bodies(range_expr)
    if explicit_notes:
        note = clean_release_note("；".join(explicit_notes[:3]))
        if not weak_release_note(note):
            return note, "commit_body"

    note = fallback_note_from_subjects(range_expr)
    if not weak_release_note(note):
        return note, "commit_subject"

    return "本次提交已上线", "fallback"


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
    release_note: str,
) -> str:
    title = "✅ {} 部署成功，已经上线" if status == "success" else "❌ {} 这次没发成，线上可能还是旧版本"
    lines = [
        "## 墨言回执",
        title.format(subject),
        "",
        "你现在只需要知道：",
    ]
    if status == "success":
        if release_note:
            lines.append(f"- 升级说明：{release_note}")
        lines.append(f"- {smoke_summary_text}")
        lines.append(f"- 回滚路径：{rollback_text}")
        if public_url:
            lines.append(f"- 产品地址：{public_url}")
        lines.append(f"- 版本：{commit_short}")
    else:
        if release_note:
            lines.append(f"- 计划升级：{release_note}")
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


def build_feishu_text(
    *,
    status: str,
    subject: str,
    public_url: str,
    run_url: str,
    commit_short: str,
    ref_name: str,
    smoke_summary_text: str,
    phase: str,
    rollback_text: str,
    release_note: str,
) -> str:
    if status == "success":
        lines = [
            f"✅ {subject} 部署完成",
        ]
        if release_note:
            lines.append(f"升级说明：{release_note}")
        lines.extend(
            [
                f"commit: {commit_short or 'unknown'}",
                f"ref: {ref_name or 'main'}",
                f"run: {run_url}",
            ]
        )
        if public_url:
            lines.append(f"public: {public_url}")
        if smoke_summary_text:
            lines.append(f"smoke: {smoke_summary_text}")
        return "\n".join(lines)

    lines = [
        f"❌ {subject} 部署失败",
    ]
    if release_note:
        lines.append(f"计划升级：{release_note}")
    lines.extend(
        [
            f"phase: {phase or 'unknown'}",
            f"commit: {commit_short or 'unknown'}",
            f"ref: {ref_name or 'main'}",
            f"run: {run_url}",
            f"rollback: {rollback_text}",
        ]
    )
    if public_url:
        lines.append(f"public: {public_url}")
    return "\n".join(lines)


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
    ref_name = os.getenv("GITHUB_REF_NAME", "")
    subject = args.subject or repo_name(github_repository or "deploy")

    rollback_attempted = to_bool(args.rollback_attempted)
    rollback_result = args.rollback_result.strip()
    smoke_summary_text = smoke_summary(smoke, args.status, args.phase)
    rollback_text = rollback_summary(rollback_attempted, rollback_result)
    release_note, release_note_source = build_release_note(subject)
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
        release_note=release_note,
    )
    plain_text = build_plain_text(markdown)
    feishu_text = build_feishu_text(
        status=args.status,
        subject=subject,
        public_url=args.public_url.strip(),
        run_url=run_url,
        commit_short=commit_short or "unknown",
        ref_name=ref_name,
        smoke_summary_text=smoke_summary_text,
        phase=args.phase.strip(),
        rollback_text=rollback_text,
        release_note=release_note,
    )

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
        "ref_name": ref_name,
        "public_url": args.public_url.strip() or None,
        "health_url": args.health_url.strip() or None,
        "service_name": args.service_name.strip() or None,
        "target_path": args.target_path.strip() or None,
        "phase": args.phase.strip(),
        "phase_label": phase_label(args.phase.strip()),
        "rollback_attempted": rollback_attempted,
        "rollback_result": rollback_result or None,
        "rollback_summary": rollback_text,
        "release_note": release_note,
        "release_note_source": release_note_source,
        "smoke_summary": smoke_summary_text,
        "release_smoke": smoke or None,
        "summary_markdown": markdown,
        "receipt_text": plain_text,
        "feishu_text": feishu_text,
        "antgather_dm_text": plain_text,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    (output_dir / "deploy-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "deploy-report.md").write_text(markdown, encoding="utf-8")
    (output_dir / "release-note.txt").write_text(release_note + "\n", encoding="utf-8")
    (output_dir / "release-note-source.txt").write_text(release_note_source + "\n", encoding="utf-8")

    if args.legacy_receipt:
        Path(args.legacy_receipt).write_text(plain_text, encoding="utf-8")


if __name__ == "__main__":
    main()
