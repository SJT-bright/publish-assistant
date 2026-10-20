# 发布助手

素材准备一次，在应用里的创作平台上传视频、填写标题/文案/话题、设置横竖封面，再保存草稿。需要公开发表时，开启「网页发表」，在平台原生页面点击「发表 / 发布 / 立即投稿」。

这是作品发布工具。它与视频下载、采集工具是独立项目。

## 下载

[Mac 与 Windows 统一下载页](https://github.com/SJT-bright/publish-assistant/releases)。当前公开发布版本为 **1.7.14 / build 45**，安装包自带 Python 后台、浏览器驱动和视频处理工具，无需另装 Python、Chrome 或 FFmpeg。

从 1.7.13 起，客户端提供 GitHub Star 入口，并自动检查、下载 GitHub 最新稳定 Release 的对应系统安装包。已下载的更新由用户选择安装时间；旧客户端需先手动升级一次。安装行为、包名和维护者发布步骤见 [客户端更新说明](docs/software-updates.md)。本机候选验证与公开发布状态分开记录。

- Mac：Apple Silicon，macOS 13 及以上。将 DMG 中的「发布助手」拖入应用程序。
- Windows：Windows 10/11 x64。运行 Setup 安装，或解压 ZIP 后运行 `DraftWorkbench.exe`。
- 开源程序包没有 Apple 公证或 Windows 商业代码签名。

## 已有功能

- 应用内浏览器、独立平台会话，平台登录窗口也在应用内打开。
- 拖入视频、素材文件夹、标题、文案与封面；支持从视频进度条截取关键帧。
- 通用/横版封面放在左侧，竖版封面在右侧完整显示；封面支持右键复制。
- 共用内容与平台独立标题/文案/话题，输入时检查平台限制。
- 七个处理阶段、细分进度、百分比、所在步骤与错误信息。
- 上传前现场核对账号与素材；草稿结果不确定时保留记录，避免自动重复上传。
- 自动草稿适配：抖音、小红书、视频号、B站、快手。今日头条、西瓜提供网页入口，自动上传与草稿校验待适配。

工作台蓝色按钮是「开始上传并保存草稿」。**网页发表是用户点击平台原生按钮；目前没有一键批量公开发表。** 平台要求扫码或验证时需要完成对应认证。

## 本机数据

会话保存在当前用户的「视频草稿工作台」应用数据目录。Mac 沿用既有目录；如检测到旧版 `Downloads/social-auto-upload/.draft-workbench/workbench-state.json`，继续使用原素材/任务账本。新安装的素材账本放在应用数据目录的 `workbench/`。卸载不会主动删除这些记录。安装包与本仓库不含任何账号、Cookie、个人视频或真实发布记录。

## 构建

需要 Python 3.12、Node.js 26。Python 构建依赖见 `requirements-build.txt`，桌面依赖固定在 `desktop/package-lock.json`。

```sh
python -m venv .venv
python -m pip install -r requirements-build.txt
npm ci --prefix desktop
python desktop/build_backend.py
# Mac: MACOSX_DEPLOYMENT_TARGET=13.0 scripts/build_media.sh mac build/media
# Windows: 使用 GitHub Actions 编译的媒体工具放入 build/media/tools
node desktop/build.cjs
```

Windows 安装器：在 `desktop/` 执行 `npx electron-builder --prepackaged ../dist/发布助手-win32-x64 --win nsis --x64 --publish never`。

## 验证范围

`tests/test_packaged_app.py` 操作真实桌面可执行文件，使用临时数据和本机模拟网站，验证后台启动、七个平台页面与登录子页、64MB 文件导入、中文素材、封面截帧、视频预览、会话重启保存等。Mac 在 Apple Silicon 上验证，Windows 在 GitHub 的 Windows 原生执行器上验证。安装包测试不代表真实平台的公开发表结果，也不能保证平台之后改变页面时仍兼容。

## 来源与许可

自动上传以 [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload) 为基础，保留 MIT License。桌面工作台、草稿核验、细分进度和独立封面等为本地扩展。第三方组件与媒体工具的源码/构建说明见 [NOTICE.md](NOTICE.md)。
