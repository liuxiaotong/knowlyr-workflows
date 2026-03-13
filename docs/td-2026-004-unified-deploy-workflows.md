# TD-2026-004: 统一部署流程 Reusable Workflows

> 作者: 姜墨言 · 日期: 2026-03-14 · 状态: 设计完成，待实施

## 背景

当前 14 个仓库各自维护独立的部署 workflow（60~250 行），存在以下问题：
- 回滚逻辑重复但实现不一致（crew 用 pip freeze 快照，其他用 cp -a .prev）
- health check 等待时间不统一（3s~10s）
- auto version tag 逻辑重复 7 次
- 新项目需要复制粘贴 + 魔改，容易遗漏关键步骤

## 方案

在 `knowlyr-workflows` 仓库创建 4 个 reusable workflow + 1 个保持不变，覆盖所有部署场景。

---

### Workflow 1: `reusable-deploy-python.yml` — Python 服务部署

**适用项目**: knowlyr-id, knowlyr-ledger, antgather-backend

**标准流程**: SSH → snapshot → rsync/pip install → [migrate] → restart → health check → [rollback] → auto tag

**Inputs**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `app_dir` | string | ✅ | — | 服务器部署目录，如 `/var/www/knowlyr-id` |
| `service_name` | string | ✅ | — | systemd 服务名，如 `knowlyr-id` |
| `health_url` | string | ✅ | — | 健康检查 URL，如 `http://127.0.0.1:8100/health` |
| `health_wait` | number | ❌ | 8 | 重启后等待秒数 |
| `install_command` | string | ❌ | `pip install -q -r requirements.txt` | 依赖安装命令 |
| `run_migrations` | boolean | ❌ | false | 是否执行 alembic upgrade head |
| `rsync_excludes` | string | ❌ | `.git,.github,.env,.venv,__pycache__,.claude` | rsync 排除列表（逗号分隔） |
| `rsync_source` | string | ❌ | `./` | rsync 源路径（antgather-backend 用 `backend/`） |
| `extra_services` | string | ❌ | `""` | 额外需要重启的服务（逗号分隔） |
| `auto_tag` | boolean | ❌ | true | 是否自动打 version tag |
| `tag_prefix` | string | ❌ | `v` | tag 前缀（antgather-backend 用 `backend-v`） |
| `pre_deploy_script` | string | ❌ | `""` | 部署前在服务器执行的脚本 |
| `post_deploy_script` | string | ❌ | `""` | 部署后在服务器执行的脚本 |

**Secrets**: `SSH_KEY`, `SSH_HOST`

**标准步骤**:
1. Setup SSH
2. Snapshot (`cp -a $app_dir ${app_dir}.prev`)
3. `pre_deploy_script`（可选）
4. rsync deploy
5. Install dependencies
6. Run migrations（可选）
7. Restart service(s)
8. Health check → 失败自动 rollback
9. `post_deploy_script`（可选）
10. Auto version tag（可选）

---

### Workflow 2: `reusable-deploy-static.yml` — 静态站部署

**适用项目**: knowlyr-website

**标准流程**: build → verify → SSH rsync to staging → health check → atomic switch → nginx cache → auto tag

**Inputs**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `webroot` | string | ✅ | — | 生产目录，如 `/var/www/knowlyr.com` |
| `build_command` | string | ✅ | — | 构建命令，如 `python3 scripts/build.py` |
| `build_deps` | string | ❌ | `""` | 构建依赖安装命令 |
| `verify_files` | string | ✅ | — | 必须存在的文件列表（逗号分隔） |
| `rsync_excludes` | string | ❌ | `.git,.github,scripts,__pycache__,.claude` | rsync 排除 |
| `nginx_config` | boolean | ❌ | false | 是否管理 nginx 缓存头 |
| `auto_tag` | boolean | ❌ | true | 是否自动打 tag |

**Secrets**: `SSH_KEY`, `SSH_HOST`

**标准步骤**:
1. Checkout + build
2. Verify build artifacts
3. Setup SSH
4. rsync to staging dir (`${webroot}.new`)
5. Health check (file existence + size)
6. Atomic switch (`mv` old → .prev, new → live)
7. Nginx cache headers（可选）
8. Auto version tag

---

### Workflow 3: `reusable-deploy-nextjs.yml` — Next.js 前端部署

**适用项目**: antgather（前端）

**标准流程**: npm ci → build → SSH rsync standalone → env write → restart → health check → rollback → auto tag

**Inputs**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `app_dir` | string | ✅ | — | 服务器部署目录 |
| `service_name` | string | ✅ | — | systemd 服务名 |
| `health_url` | string | ✅ | — | 健康检查 URL |
| `health_wait` | number | ❌ | 10 | 重启后等待秒数 |
| `node_version` | number | ❌ | 22 | Node.js 版本 |
| `build_env` | string | ❌ | `""` | 构建时环境变量（JSON 格式） |
| `server_env_keys` | string | ❌ | `""` | 需要写入 .env 的 secrets 名称列表 |
| `extra_services` | string | ❌ | `""` | 额外需要重启的服务 |
| `auto_tag` | boolean | ❌ | true | 自动打 tag |

**Secrets**: `SSH_KEY`, `SSH_HOST` + 动态 env secrets

---

### Workflow 4: `reusable-publish-pypi.yml` — PyPI 发布

**适用项目**: knowlyr-crew, data-recipe, data-synth, data-check, model-audit, knowlyr-gym

**Inputs**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `python_version` | string | ❌ | `"3.12"` | Python 版本 |
| `build_tool` | string | ❌ | `uv` | 构建工具：`uv` 或 `setuptools` |
| `run_tests` | boolean | ❌ | false | 发布前是否跑测试 |
| `test_command` | string | ❌ | `pytest` | 测试命令 |
| `monorepo_packages` | string | ❌ | `""` | monorepo 子包路径列表（knowlyr-gym 用） |

**Secrets**: 无（使用 trusted publisher）

**标准步骤**:
1. Checkout
2. Setup Python
3. Run tests（可选）
4. Build (`uv build` 或 `python -m build`)
5. Publish via trusted publisher

---

### 不纳入统一的特殊 workflow

| 项目 | 原因 |
|------|------|
| knowlyr-crew deploy | 蓝绿双实例、pip install from git、config 写入、crontab、审计 — 独特性太高 |
| knowlyr-crew-private deploy | git pull + config sync 模式，与标准 rsync 部署不同 |
| knowlyr-theme publish | VS Code Marketplace 发布（vsce），与 PyPI 无关 |

---

## 迁移映射

| # | 仓库 | 当前 | 迁移到 |
|---|------|------|--------|
| 1 | knowlyr-id | deploy.yml 90 行 | `reusable-deploy-python.yml` caller |
| 2 | knowlyr-ledger | deploy.yml 100 行 | `reusable-deploy-python.yml` caller |
| 3 | antgather (backend) | deploy-backend.yml 130 行 | `reusable-deploy-python.yml` caller |
| 4 | antgather (frontend) | deploy.yml 180 行 | `reusable-deploy-nextjs.yml` caller |
| 5 | knowlyr-website | deploy.yml 200 行 | `reusable-deploy-static.yml` caller |
| 6 | knowlyr-crew | deploy.yml 170 行 | **保持不变** |
| 7 | knowlyr-crew-private | deploy.yml 70 行 | **保持不变** |
| 8 | data-recipe | release.yml | `reusable-publish-pypi.yml` caller |
| 9 | data-synth | publish.yml | `reusable-publish-pypi.yml` caller |
| 10 | data-check | (缺失) | `reusable-publish-pypi.yml` caller（新增） |
| 11 | model-audit | publish.yml | `reusable-publish-pypi.yml` caller |
| 12 | knowlyr-gym | publish.yml | `reusable-publish-pypi.yml` caller |
| 13 | knowlyr-crew | release.yml | `reusable-publish-pypi.yml` caller |
| 14 | knowlyr-theme | publish.yml | **保持不变**（vsce） |

## 实施顺序

1. **Phase 1**: 创建 `reusable-publish-pypi.yml`（最简单，无 SSH 依赖） → 迁移 6 个 publish caller
2. **Phase 2**: 创建 `reusable-deploy-python.yml` → 迁移 knowlyr-id, knowlyr-ledger, antgather-backend
3. **Phase 3**: 创建 `reusable-deploy-nextjs.yml` → 迁移 antgather 前端
4. **Phase 4**: 创建 `reusable-deploy-static.yml` → 迁移 knowlyr-website

每个 Phase 完成后用测试 PR 验证 workflow 能正常触发。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Secret 名称不统一（crew 用 `DEPLOY_SSH_KEY`/`DEPLOY_HOST`，其他用 `SERVER_SSH_KEY`/`SERVER_HOST`） | Caller 通过 `secrets:` 映射统一名称 |
| antgather 前端 env 写入涉及多个 secrets | `server_env_keys` 参数 + secrets inherit |
| knowlyr-website 的 nginx 配置高度定制 | 作为 `post_deploy_script` 传入，或保留在 caller 侧 |
| 迁移期间某个项目部署失败 | 每个 Phase 逐个迁移 + 测试，失败立即回退到旧 workflow |
