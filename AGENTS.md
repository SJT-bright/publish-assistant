# 发布助手公开源码

- 应用入口：desktop/main.cjs、draft_workbench.py；版本：1.7.14 / build 45。
- Mac Apple Silicon 与 Windows x64 使用同一份源码，打包包含 Python 后台、浏览器驱动和媒体工具。
- 平台：抖音、小红书、视频号、B站、快手；今日头条与西瓜目前仅提供网页入口。
- 保持既有用户资料、素材和任务账本；正式资料与独立 QA 会话必须隔离。
- 投稿提交回执不等于审核或公开；未知结果禁止自动重复提交。
- GitHub Star 与客户端更新机制见 docs/software-updates.md。
- 客户端启动 10 秒后与每 4 小时检查最新稳定 Release；完整包 SHA-256/大小校验通过后由用户选择安装。
- 正在执行任务时禁止安装；旧客户端需手动升级一次以启用更新机制。
- 本地模拟测试、原生包验证和真实平台发帖是不同证据层次。
- 公开发布必须排除账号、Cookies、日志、素材、任务账本、私人备份与原始真实验收记录。
- Windows 原生构建、安装验收、Release 上传和下载回验使用 .github/workflows/windows-release.yml。
- tests/test_packaged_app.py 必须使用独立用户资料与本机模拟平台；测试结束关闭自己的 App、后台与服务。
