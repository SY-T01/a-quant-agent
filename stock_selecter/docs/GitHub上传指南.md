# 把项目上传到自己的GitHub

建议先建立私有仓库，作为程序源码和版本备份。现有行情、数据库、原始公告、AI正文、个人资金偏好及报告继续留在本机外置磁盘。上传源码不等于备份这些运行资料。

## 已整理的上传范围

|上传|保留本机，不上传|
|---|---|
|stock_selector程序、tests测试|data全部运行资料、reports报告与AI存档|
|README、docs使用说明|原始Word、PDF等用户文件|
|configs中的公开规则和example示例|research_preferences.json中的个人资金设置|
|股票研究助手.app及.launcher入口|pdf.json中的本机Python和字体路径|
|.gitignore|.env、密钥文件、缓存和数据库|

忽略规则已经写入.gitignore，不会删除被排除文件，也不会影响当前程序使用。不要使用git add -f强行加入被排除的资料；不要把整个项目压缩后上传，以免绕过这些规则。

## 用GitHub Desktop上传

1. 安装[GitHub Desktop](https://desktop.github.com/)，登录自己的GitHub账号。密码、验证码和令牌只在官方登录流程中输入，不发送到聊天。
2. 选择File → Add Local Repository，选择本项目文件夹：`/Volumes/tsy_macmini/work/stock/stock_selecter`。
3. 目前项目还没有Git仓库。如果提示不是Git仓库，点击create a repository here instead。确认最终位置仍是本项目文件夹，不是里面又嵌套一个同名文件夹。已有README和.gitignore均保留，不另选模板覆盖；License可先选None。
4. 在Changes列表中检查待提交内容。应该看到程序、测试、说明和配置示例，不应该看到data、reports、个人资金配置、pdf.json或原始Word。
5. 如仍有待提交改动，填写说明，例如“Initial source import”，点击Commit to main；若创建仓库时已自动完成首次提交，则无需重复提交。
6. 点击Publish repository。仓库名称可用`a-share-research`，描述可写“A股行业与公司研究辅助系统”。保留Keep this code private勾选，再点击Publish repository。
7. 点击View on GitHub，确认仓库位于自己的账号下且标为Private。

GitHub官方步骤：[添加并发布已有项目](https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-an-existing-project-to-github-using-github-desktop)、[创建第一个仓库](https://docs.github.com/en/desktop/overview/creating-your-first-repository-using-github-desktop)。

## 以后如何更新

修改程序并完成检查后，打开GitHub Desktop，查看Changes，填写本轮改动说明，Commit to main，再点击Push origin。只有提交并推送的源码变化会进入GitHub；本地数据和个人报告仍被排除。

## 换电脑或公开分享前

克隆仓库后，需要Python 3.9或更新版本。核心程序与测试主要使用标准库，可在项目目录运行：

```bash
python3 -B -m unittest discover -s tests -q
python3 -B -m stock_selector.launcher
```

按需将configs/research_preferences.example.json复制为configs/research_preferences.json，再在菜单8、9中设置自己的持有期和资金。配置示例没有个人预算或密钥。数据为空时需先进行数据更新，公开接口可能失败，不会自动补出数据。

PDF需要ReportLab和本机中文字体。pdf.example.json中的路径只是占位符，应复制为pdf.json并填写本机实际Python和字体位置。本项目当前还含原外置磁盘的周更新检查，换路径或跨平台使用前应进一步整理；源码上传成功不代表已成为跨电脑免安装的软件。

当前整理用于私有源码备份，没有替你选择开源授权、创建远程仓库或上传文件。将来公开前，可再整理通用安装配置、使用示例和许可证。原来Codex中的每周自动任务也不会仅因上传代码而变成GitHub自动任务。
