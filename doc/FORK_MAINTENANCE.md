# Fork 上游同步与升级

本 fork 保留 Tulpa 上游的许可证、版权声明和第三方归属。自有补丁与上游提交共同保存在 Git 历史中；`fork-manifest.json` 记录仓库身份、分支、补丁范围和必须运行的检查。不要把本地授权、聊天数据、登录状态或下载内容提交到仓库。

## 检查与合并

在已有 Python 开发依赖、Git 和 PowerShell 7 的环境中，从仓库根目录运行：

```powershell
# 默认只检查工作区和远程身份，fetch 两个 main，并展示领先/落后数量。
pwsh -NoProfile -File scripts/sync-upstream.ps1

# 明确合并上游，并运行 manifest 中的 fork 检查；不推送。
pwsh -NoProfile -File scripts/sync-upstream.ps1 -Apply -Python .venv/Scripts/python.exe

# 合并、检查通过后推送 origin/main。
pwsh -NoProfile -File scripts/sync-upstream.ps1 -Apply -Push -Python .venv/Scripts/python.exe

# 已完成本地合并时，重新检查并推送。
pwsh -NoProfile -File scripts/sync-upstream.ps1 -Push -Python .venv/Scripts/python.exe
```

Linux 安装 PowerShell 7 后也可使用该脚本，Python 参数改为 `.venv/bin/python`。脚本自身不安装依赖、不调用模型、不发送聊天消息、不启动真实 QQ 服务。

`origin` 必须指向 manifest 中的 fork，`upstream` 必须指向原 Tulpa 仓库。HTTPS、`git@github.com:owner/repository.git` 和标准 `ssh://git@github.com/owner/repository.git` 都可识别。脚本拒绝嵌入凭据的远程 URL、意外 pushurl、多个远程目标、非 main 分支、未提交改动、未完成的 Git 操作和空分支。默认 fetch 只更新远程跟踪引用，不改当前提交或工作文件。

`-Apply` 使用 `git merge --no-edit upstream/main`。双方独立改动会保留；有分歧时生成合并提交，所有自有补丁仍可从历史追溯。`-Push` 必须通过 fork 检查，当前分支必须包含 fetch 到的 origin/main 与 upstream/main，并只执行普通 main 推送。服务器在 fetch 后又产生新提交时，普通推送会拒绝覆盖它们。

## 冲突与检查失败

发生冲突时，脚本返回失败，留下 Git 的冲突文件和 MERGE_HEAD，且不会运行后续检查或推送。用 `git status` 查看文件，逐处将上游改动与本 fork 行为一起保留，再 `git add` 和 `git commit` 完成合并。随后重新运行 `-Apply` 或 `-Push`。决定取消这次合并时，可在检查现场后手动执行 `git merge --abort`。脚本不会使用整文件 theirs、reset、rebase 或 force push，也不承诺未来冲突都能自动解决。

如果 origin/main 包含本地没有的提交，先正常整合这些 fork 提交，再同步上游。检查失败时合并提交仍在本地；修正对应问题并提交后重新运行。检查脚本留下未提交文件或改变 HEAD 时也会阻止推送。无需重新手敲补丁；冲突应在完整 fork 历史上解决。

## 从 fork 构建后升级

上游发布的新安装包可能不包含本 fork 的原生文件、图片读取/下载与并行消息补丁。不要直接用上游安装包覆盖自有 fork 的代码。先 fetch、merge、解决冲突并通过 manifest 检查，再按仓库现有构建流程从 fork 提交构建安装包；遵循原有备份和安装要求部署该包，并单独验证真实运行环境。源码检查通过不代表安装包已构建、服务已重启或 QQ 实时链路已验收。

新增自有行为时，更新 manifest 的补丁范围及有意义的隔离回归检查。维护脚本不会自动执行所有上游 CI，也不会替代对应变更需要的构建或运行验证。仓库没有定时同步或后台自动推送任务；每次同步由操作者明确执行。

## 同步脚本回归

```powershell
python scripts/check_fork_upstream_sync.py
```

该检查只使用临时本地 bare 仓库和临时工作区，通过 Git URL 重写将测试身份映射到本地，不访问网络或真实远端。覆盖预览只 fetch、分歧合并保留双方文件和父提交、明确请求后推送、冲突保留现场、脏工作区在 fetch 前拒绝、远程/凭据/pushurl/分支保护、检查失败阻止推送，以及未整合 upstream 或 origin 时拒绝推送。
