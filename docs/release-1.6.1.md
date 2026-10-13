# 发布助手 1.6.1 / build 28

当前交付的应用源码基线：`d32b7b94e9bae913c4b04da75596c4c184784491`。

[统一 Release](https://github.com/SJT-bright/publish-assistant/releases/tag/v1.6.1) · [Windows 原生构建与验收](https://github.com/SJT-bright/publish-assistant/actions/runs/38339238798)

Mac arm64 在本机 macOS 上操作真实应用完成 16 项检查，并在只读 DMG 中重复完成 16 项。检查包括启动、七个平台会话隔离与重启保留、64MB 文件传入控件、登录子页、中文素材、截帧、预览和数据持久化；运行后签名保持有效。最低系统声明为 macOS 13，媒体工具的部署目标已核对为 13.0；没有额外宣称在 macOS 13 实机验收。

Windows x64 在原生 Windows 执行器上完成共享界面与辅助组件检查，运行包和安装后的程序分别通过 15 项实际操作检查。运行中不向安装目录写日志或新增文件。上传后的安装器和 ZIP 已在 Windows 执行器完整重新下载并比较 SHA-256。

源码工作台扩展保持既有自动草稿流程与网页手动发表。自动保存草稿支持五个平台；今日头条、西瓜的自动上传仍待适配。本轮只用本机模拟平台，不能据此宣称真实账号已公开发布。

本机正式安装的界面、版本及后台已核对。素材编辑、任务账本、历史记录保留，六个既有平台 Cookie 文件与更新前备份相同。备份为私有资料，不在仓库或 Release 中。

Mac 包含 DMG / ZIP，Windows 包含 Setup / ZIP，均提供独立校验和与验收 JSON。开源包采用 Mac ad-hoc 签名，没有 Apple 公证和 Windows 商业签名。
