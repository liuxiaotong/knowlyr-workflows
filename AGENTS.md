# knowlyr-workflows Release Contract

这个仓库不是业务应用本身，而是共享发布基础设施：

- 维护可复用的 GitHub Actions 部署工作流
- 维护发布回执生成逻辑
- 供其他仓库通过 `workflow_call` 调用

## 变更原则

1. 向后兼容优先
   - 不要随意删输入参数、改默认值、改输出字段名。
   - 任何破坏性改动，都要先确认所有调用方能一起升级。

2. 发布路径只改工具，不改制度
   - 这里的职责是提供标准化工作流和回执工具，不在仓库内自创新的正式发布制度。
   - 正式发布路径仍以中央 deploy policy 和各仓库自己的 release contract 为准。

3. 可读、可复查、可回滚
   - workflow 变更要尽量小步提交。
   - 任何会影响下游仓库上线流程的改动，都要写清楚变更原因和回滚思路。

4. 避免污染仓库
   - 不提交 `__pycache__`、临时文件、构建产物和本地机器残留。

## 提交前最低校验

改动 workflow 或发布脚本后，至少跑：

```bash
actionlint .github/workflows/*.yml
python3 -m py_compile scripts/deploy/write_report.py
```

如果改了共享 workflow 的输入、输出或回执格式，再额外检查下游仓库兼容性。
