# 将整合项目上传到 GitHub

仓库根目录应是同时包含 `A_stock` 和 `stock_selecter` 的 `stock` 文件夹。这样两个模块的相对位置保持一致。推荐先新建私有仓库 `a-quant-agent`，作为个人源码备份；公开分享前再单独整理安装流程和许可证。

## 已准备的上传范围

上传两个模块的源码、测试、公开配置、演示样例、使用说明和应用构建脚本。根目录及各子项目的 `.gitignore` 会排除真实持仓、邮箱与模型密钥、个人资金偏好、行情原始数据、研究报告、AI存档、虚拟环境及本机构建的 `.app`。

这份源码备份不包含当前运行数据。若要备份持仓和设置，应另外保存在自己的受信任存储中，不要将整个现有工作目录直接压缩上传。

## 连接 GitHub 后代为上传

在 Codex 提供的 GitHub 连接界面完成官方登录与授权。密码、验证码和访问令牌只在 GitHub 官方界面中输入，无需发送到聊天。连接成功后，确认目标账号、仓库名称及公开/私有范围，即可将准备好的源码提交到仓库。连接本身不等于已经上传代码。

GitHub CLI 也支持浏览器登录。如果以后使用命令行，可安装官方 CLI 后运行 `gh auth login --web --hostname github.com`，在官方网页确认授权，再用 `gh auth status` 检查结果。参见 [GitHub CLI 登录说明](https://cli.github.com/manual/gh_auth_login)。

## 用本机 GitHub Desktop 上传

本机已安装 GitHub Desktop，也可以按下列步骤操作：

1. 打开 GitHub Desktop，在设置的 Accounts 页面登录自己的 GitHub。
2. 选择 File → Add Local Repository，选中当前 `stock` 文件夹。若提示尚无 Git 仓库，选择在此创建仓库，保留已有 README 和忽略规则；不要再嵌套一层同名文件夹。
3. 检查 Changes 列表。应看到 `A_stock/aquant`、`stock_selecter/stock_selector` 等源码，不应出现 `runtime`、`data`、`reports`、真实资金配置或密钥。
4. 创建首次提交，然后选择 Publish repository。名称可用 `a-quant-agent`，保留 Keep this code private，再发布。
5. 到 GitHub 网页确认仓库归属和 Private 标记。以后修改代码后，Commit，再 Push origin。

官方说明：[上传本地代码到 GitHub](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github?platform=mac)。

## 源码压缩包

在当前项目根目录执行：

```sh
A_stock/.venv/bin/python scripts/package_source.py
```

默认输出 `exports/a-quant-agent-source.zip`。也可使用 Python 3.11 及以上运行。导出遵守根目录和两个子项目的忽略规则，对比本地已配置凭据，并校验压缩包完整性及每个源文件的 SHA-256。已存在的同名包不会被自动覆盖，需使用 `--output` 指定新文件名。

压缩包适合留档或传输；若要在 GitHub 中正常查看代码和保留改动历史，应解压后把源码文件提交进仓库，而不是仅上传一个 ZIP 文件。

## 下载源码后如何恢复运行

当前仍是依赖本机 Python 环境的程序。源码包保留完整应用构建脚本，现有编译好的 `.app` 带有本机绝对路径，换电脑需在新位置重建，不能直接复用旧应用。

1. 安装带 Tk 支持的 Python 3.11 或以上，在 `A_stock` 中创建 `.venv`。
2. 按实际使用需要安装 AKShare、Pandas、BaoStock；首次构建应用图标需要 Pillow，PDF 需要 ReportLab 和中文字体。基础规则演示仅使用标准库。
3. 在 macOS 安装命令行开发工具后，进入 `A_stock`，运行 `.venv/bin/python scripts/build_mac_app.py` 重建应用。
4. 打开新应用，重新填写本机邮箱、密钥及实际持仓。研究缓存为空时需重新更新数据。

部分 macOS 原生测试依赖已配置的 `.venv` 和本机应用入口，应先完成上述准备。旧 `stock_selecter` 菜单的应用包属于本机构建产物，统一入口以重建的 A-Quant 为准。

原来的周更新还包含本机外置磁盘路径检查，换电脑或移动项目后需要调整。源码备份不会自动迁移系统权限、定时任务或本机凭据。
