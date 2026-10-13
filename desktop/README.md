# 发布助手桌面运行包

`main.cjs` 提供同一窗口内的工作台、七个平台浏览器和认证子页。`runtime-paths.cjs` 为 Mac/Windows 分配可写用户状态目录，打包运行时从 Resources 中启动内置后台。源码开发仍可使用 `.venv`。

`backend_entry.py` 是冻结后台入口，支持 server / runner。`build_backend.py` 同时打包网页资源和所需驱动。`build.cjs` 生成包含该后台和 FFmpeg/FFprobe 的独立 Electron 应用。Windows 使用原生标题栏，Mac 保留原侧栏、玻璃界面与会话目录。

所有平台按独立持久分区保存 Cookie、localStorage 与 IndexedDB。后台连接精确的 Chromium target ID，文件通过本机 CDP 传给指定上传控件，避免超过 50MB 时通过远程缓冲区传输失败。

草稿失败保留原素材和任务证据。网页发表只有在用户开启此模式、当前没有自动任务时允许点击平台的原生发表按钮。工作台不提供自动批量公开发表。

验收命令：`python tests/test_packaged_app.py --app <打包后的目录或 Mac .app> --output <结果.json>`。环境变量 SAU_DESKTOP_TEST 只在验收时启用本机模拟平台与受鉴权的测试退出入口。
