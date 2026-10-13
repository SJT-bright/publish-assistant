# 发布助手第三方组件

发布助手的自动上传适配基于 [dreammis/social-auto-upload](https://github.com/dreammis/social-auto-upload)，保留原 MIT License。

桌面壳使用 Electron 44.5.1，运行包包含其 LICENSE 与 Chromium LICENSES 文件。后台使用 Python、PyInstaller、Patchright、Playwright、Pillow、Requests、Loguru；各组件许可证随打包运行环境提供。

视频探测和封面截图使用独立进程 FFmpeg 9.0.1 / FFprobe，采用 LGPL 2.1 构建，不启用 GPL / nonfree 配置。FFmpeg 和 zlib 的精确源码、校验值与重新构建脚本位于本仓库 `Vendor/MediaSources/`、`scripts/build_media.sh`。FFmpeg 的 LGPL 许可证随工具一起分发，用户可以自行替换安装目录中的媒体工具。
