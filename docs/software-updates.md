# GitHub Star 与客户端更新

从 1.7.13 / build 44 起，左下角提供 GitHub Star 按钮，打开固定项目地址 https://github.com/SJT-bright/publish-assistant 。用户在 GitHub 登录后自行点击 Star。

正式客户端启动 10 秒后检查公开的最新 GitHub Release，运行期间每 4 小时再检查一次；也可以点击左下角或应用菜单中的“检查软件更新”。发现更高的稳定版本后自动下载与当前系统、CPU 匹配的完整包。断网或 GitHub 限流会显示重试入口，后续定时检查仍会继续。

下载时校验 GitHub Release API 提供的 SHA-256 和文件大小。预发布、草稿、同版和较旧版本不会安装；缺少当前系统安装包或校验信息时停止更新。客户端不需要 GitHub Token。旧客户端必须先手动安装一次 1.7.13 或更高版本，才能启用此机制。

下载完成后点击“安装 v版本号”，确认安装。上传、发布、登录或页面操作进行中不会安装。Mac 会验证 ZIP 结构、Bundle ID、版本、CPU 和代码签名，在原安装位置暂存新 App，等待旧 App 退出、核对并停止本 App 的后台，然后替换、保留旧 App 回滚副本并重新打开。Windows 会在旧 App 退出、核对并停止所属后台后打开新版 NSIS 安装向导，用户选择现有安装位置。不覆盖浏览器资料、素材编辑、账号或任务账本。

## 发布新版本

客户端读取 GitHub 的 `/repos/SJT-bright/publish-assistant/releases/latest`。仅 push 源码不会触发更新。一次完整的更新发布应按以下顺序完成：

1. 修改 `desktop/package.json` 与 lockfile 的三段数字版本，并增加 `desktop/build.cjs` 的 build 号。
2. 在相应系统构建，运行定向测试和 `tests/test_packaged_app.py`，使用独立会话和本地模拟平台。
3. Mac 执行 `python3 scripts/package_mac_update.py --app dist/发布助手-darwin-arm64/发布助手.app --arch arm64`，得到包含根目录 `发布助手.app/` 的 ZIP。Intel 版必须在对应环境构建并使用 `--arch x64`，不可把 arm64 包改名。
4. Windows workflow 从 package.json 自动读取版本，生成 NSIS 安装包并验证安装结果。需要上传时传入精确的 `v版本号`，目标必须是同一源码提交的 draft Release。
5. 向同一个 draft Release 上传已验证的各系统安装包、校验文件和公开的脱敏验收结果；所有目标系统包齐备后发布为最新稳定 Release。

自动更新所需的文件名必须精确匹配：

| 系统 | 文件名示例 |
| --- | --- |
| Apple Silicon Mac | `Publish-Assistant-1.7.13-Mac-arm64.zip` |
| Intel Mac（如有构建） | `Publish-Assistant-1.7.13-Mac-x64.zip` |
| Windows x64 | `Publish-Assistant-1.7.13-Windows-x64-Setup.exe` |

GitHub 上传完成后由平台提供 asset 的 `digest: sha256:...`；缺失 digest 的包不会下载。Release 标签必须是 `v1.7.13` 或 `1.7.13` 这类与包版本一致的三段数字版本。完整包可同时给首次安装用户下载，无需额外更新服务器或独立更新清单。

旧 Mac App 的回滚副本保存在安装目录下 `.发布助手-previous-时间戳.app`；安装过程日志保存在用户资料目录的 `software-updates/install-*/installation.log`。这些只留在用户本机，不上传仓库。回滚备份应在新版运行确认后手动处理。

源码测试与 Mac 本机验收不能替代 Windows 原生运行结果、实际用户远端更新或社交平台投稿验收。
