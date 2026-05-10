from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRITE_REPORT = ROOT / "scripts" / "deploy" / "write_report.py"
NOTIFY_REPORT = ROOT / "scripts" / "deploy" / "notify_report.py"

spec = importlib.util.spec_from_file_location("notify_report", NOTIFY_REPORT)
notify_report = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(notify_report)


class DeployReportScriptTests(unittest.TestCase):
    def test_write_report_exports_channel_texts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            smoke_path = tmp_path / "release-smoke.json"
            smoke_path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "summary": "上线后检查通过（HTTP 200）",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "GITHUB_REPOSITORY": "liuxiaotong/antgather",
                "GITHUB_SHA": "abcdef0123456789",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_REF_NAME": "main",
                "GITHUB_EVENT_NAME": "push",
                "DEPLOY_RELEASE_NOTE": "本次发布新增项目列表筛选入口",
            }
            subprocess.run(
                [
                    sys.executable,
                    str(WRITE_REPORT),
                    "--output-dir",
                    str(tmp_path / "artifacts"),
                    "--status",
                    "success",
                    "--subject",
                    "AntGather 前端",
                    "--public-url",
                    "https://antgather.knowlyr.com",
                    "--health-url",
                    "http://127.0.0.1:3100/projects",
                    "--service-name",
                    "antgather",
                    "--target-path",
                    "/var/www/antgather.knowlyr.com",
                    "--phase",
                    "completed",
                    "--rollback-attempted",
                    "false",
                    "--rollback-result",
                    "not_needed",
                    "--smoke-file",
                    str(smoke_path),
                    "--legacy-receipt",
                    str(tmp_path / "deploy-receipt.txt"),
                ],
                check=True,
                env=env,
            )

            report = json.loads((tmp_path / "artifacts" / "deploy-report.json").read_text(encoding="utf-8"))
            self.assertIn("✅ AntGather 前端 部署完成", report["feishu_text"])
            self.assertIn("升级说明：本次发布新增项目列表筛选入口", report["feishu_text"])
            self.assertIn("升级说明：本次发布新增项目列表筛选入口", report["antgather_dm_text"])
            self.assertEqual(report["release_note"], "本次发布新增项目列表筛选入口")
            self.assertEqual(report["release_note_source"], "manual")
            self.assertIn("commit: abcdef0", report["feishu_text"])
            self.assertIn("run: https://github.com/liuxiaotong/antgather/actions/runs/12345", report["feishu_text"])
            self.assertIn("墨言回执", report["antgather_dm_text"])
            self.assertEqual(report["health_url"], "http://127.0.0.1:3100/projects")
            self.assertEqual(
                (tmp_path / "artifacts" / "release-note.txt").read_text(encoding="utf-8").strip(),
                "本次发布新增项目列表筛选入口",
            )

    def test_notify_report_dry_run_writes_delivery_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            report_path = tmp_path / "deploy-report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "repo": "liuxiaotong/antgather",
                        "status": "success",
                        "subject": "AntGather 前端",
                        "sha": "abcdef0",
                        "commit_short": "abcdef0",
                        "ref_name": "main",
                        "run_url": "https://github.com/liuxiaotong/antgather/actions/runs/12345",
                        "commit_url": "https://github.com/liuxiaotong/antgather/commit/abcdef0",
                        "public_url": "https://antgather.knowlyr.com/projects",
                        "health_url": "http://127.0.0.1:3100/projects",
                        "service_name": "antgather",
                        "target_path": "/var/www/antgather.knowlyr.com",
                        "smoke_summary": "上线后检查通过（HTTP 200）",
                        "release_note": "本次发布新增项目列表筛选入口",
                        "feishu_text": "✅ AntGather 前端 部署完成",
                        "antgather_dm_text": "墨言回执\n✅ 部署成功，已经上线",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config_path = tmp_path / "feishu.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        "bots:",
                        "  - bot_id: moyan",
                        '    app_id: "app-id"',
                        '    app_secret: "app-secret"',
                        '    owner_open_id: "ou_xxx"',
                        "    primary: true",
                    ]
                ),
                encoding="utf-8",
            )
            env_path = tmp_path / ".env"
            env_path.write_text("ANTGATHER_INTERNAL_TOKEN=token\n", encoding="utf-8")
            env = {
                **os.environ,
                "DEPLOY_NOTIFY_FEISHU": "true",
                "DEPLOY_NOTIFY_ANTGATHER": "true",
                "FEISHU_CONFIG_PATH": str(config_path),
                "ANTGATHER_ENV_PATH": str(env_path),
            }
            subprocess.run(
                [
                    sys.executable,
                    str(NOTIFY_REPORT),
                    "--report-json",
                    str(report_path),
                    "--output-dir",
                    str(tmp_path / "out"),
                    "--dry-run",
                ],
                check=True,
                env=env,
            )

            combined = json.loads((tmp_path / "out" / "deploy-notifications.json").read_text(encoding="utf-8"))
            self.assertEqual(combined["channels"]["feishu"]["status"], "accepted")
            self.assertEqual(combined["channels"]["antgather"]["status"], "accepted")
            self.assertEqual(combined["channels"]["feishu"]["msg_type"], "interactive")
            self.assertEqual(combined["channels"]["feishu"]["card_title"], "✅ AntGather 前端 部署完成")
            self.assertEqual(combined["channels"]["antgather"]["sub_type"], "assistant_receipt")
            self.assertEqual(combined["channels"]["antgather"]["card_type"], "receipt")
            self.assertTrue(combined["channels"]["antgather"]["native_card"])
            self.assertTrue((tmp_path / "out" / "feishu-notification.json").exists())
            self.assertTrue((tmp_path / "out" / "antgather-notification.json").exists())

    def test_notify_report_builds_native_cards(self) -> None:
        report = {
            "status": "success",
            "subject": "AntGather 后端 API",
            "sha": "3240c9165e4190e0252a247c9ad1ff7bdb26e5a2",
            "commit_short": "3240c91",
            "commit_url": "https://github.com/liuxiaotong/antgather/commit/3240c916",
            "run_url": "https://github.com/liuxiaotong/antgather/actions/runs/25629016854",
            "ref_name": "main",
            "public_url": "https://antgather.knowlyr.com/api/health",
            "health_url": "http://127.0.0.1:8200/health",
            "service_name": "antgather-api.service",
            "target_path": "/var/www/antgather-api",
            "release_note": "修复生产多机位冒烟脚本的后端发布目录定位逻辑",
            "smoke_summary": "AntGather 后端蓝绿切换后健康检查与三视角核心 smoke 均通过",
        }

        feishu_card = notify_report.build_feishu_card(report)
        self.assertEqual(feishu_card["header"]["template"], "green")
        self.assertEqual(feishu_card["header"]["title"]["content"], "✅ AntGather 后端 API 部署完成")
        self.assertIn("lark_md", json.dumps(feishu_card, ensure_ascii=False))
        self.assertIn("查看发布记录", json.dumps(feishu_card, ensure_ascii=False))

        antgather_card = notify_report.build_antgather_receipt_card(report)
        self.assertEqual(antgather_card["actor"], "墨言")
        self.assertEqual(antgather_card["tone"], "success")
        self.assertEqual(antgather_card["title"], "AntGather 后端 API 部署成功，已经上线")
        self.assertEqual(antgather_card["primary_url"], report["run_url"])
        self.assertEqual(antgather_card["version"], "3240c91")
        self.assertTrue(any(section["title"] == "你现在只需要知道" for section in antgather_card["sections"]))

    def test_notify_report_builds_silt_style_feishu_fallback(self) -> None:
        text = notify_report.report_text(
            {
                "status": "success",
                "subject": "Sentinel",
                "sha": "1234567890",
                "ref_name": "main",
                "run_url": "https://github.com/liuxiaotong/knowlyr-sentinel/actions/runs/1",
                "public_url": "https://sentinel.knowlyr.com",
                "smoke_summary": "上线后检查通过（HTTP 200）",
                "release_note": "发布通知会自动概括本次上线内容",
                "receipt_text": "墨言回执\n这是一段较长回执",
            },
            "feishu_text",
        )
        self.assertIn("✅ Sentinel 部署完成", text)
        self.assertIn("升级说明：发布通知会自动概括本次上线内容", text)
        self.assertIn("commit: 1234567", text)
        self.assertIn("ref: main", text)
        self.assertIn("smoke: 上线后检查通过（HTTP 200）", text)
        self.assertNotIn("墨言回执", text)


if __name__ == "__main__":
    unittest.main()
