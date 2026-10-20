# 发布助手 1.7.14 / build 45

GitHub Star 入口位于侧栏底部。客户端启动 10 秒后及每 4 小时检查新稳定版，自动下载对应系统/CPU 的完整包并校验 GitHub API 提供的 SHA-256；下载完成后由用户选择安装时间。任务执行中禁止安装。旧版客户端需先手动升级一次。

本版同时包含主页草稿清空与撤销、当前账号验证绑定、平台上传与原生消费链核验、视频号草稿列表等待/迟到响应隔离、平台页面显示修复和可恢复的本机后台连接。

Mac Apple Silicon 使用完整 App ZIP 与 DMG；Windows x64 使用 NSIS 安装包与便携 ZIP。两者均包含后台、浏览器驱动和媒体工具，无需另装 Python、Chrome 或 FFmpeg。

Release 中的 Mac-validation.json、Windows-validation.json、Windows-installer-validation.json 与 Release-verification.json 记录各自的真实构建和本地模拟验证结果。安装包验证不代表真实平台的投稿或审核结果。

构建脚本：desktop/build_backend.py、desktop/build.cjs、scripts/package_mac_update.py 与 Windows 原生 workflow。客户端更新约定见 [软件更新](software-updates.md)。
