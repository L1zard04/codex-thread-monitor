# Codex Thread Monitor · 四格对话监视器

**同时关注四个 Codex 对话：看状态、读最新回答，一键回到原对话。**

一个面向 Windows 的轻量桌面伴随工具。用可置顶、可缩放的 2×2 方格查看不同项目的回答进度，减少在多个对话之间来回切换。使用 Python 标准库实现，无第三方 Python 依赖，无需 API Key；并非 OpenAI 官方插件。

| 功能 | 说明 |
| --- | --- |
| 四格监视 | 每格独立选择项目和对话 |
| 状态颜色 | 白色未选择，黄色回答中，绿色当前轮完成，灰色未知或中断 |
| 最新结果 | 自动更新最新完成回答，全文可滚动查看 |
| 快速返回 | 每格“打开”按钮跳回原对话 |
| 自由缩放 | 拖动边框时四格保持等大的正方形 |
| 自动联动 | 可选随 Codex 打开，单实例保护，恢复原有选择 |

## 环境要求

- Windows 10 / 11。
- Python **3.10 或更新版本**，包含 tkinter；可用 `python -m tkinter` 检查。
- 本机安装 Codex 桌面应用，并有可读取的本地会话记录。
- 自动联动目前识别 Windows Store 版 Codex 桌面包；VS Code 扩展、CLI 和普通 ChatGPT 安装不作为桌面启动信号。

## 启动

通过本仓库 **Code → Download ZIP** 下载并解压，或使用 Git：

```powershell
git clone https://github.com/L1zard04/codex-thread-monitor.git
cd codex-thread-monitor
python app.py
```

首次启动四格为空。点击 ＋，选择项目，再搜索并选择具体对话。窗口默认置顶。

### 无控制台的双击启动

在项目目录执行一次：

```powershell
python configure.py
```

之后双击 `launch.vbs`。这会生成仅用于你这台电脑的 `python-path.txt`，支持 Unicode 路径；不要将此文件提交到仓库。

换电脑或移动 Python 后，重新运行 `python configure.py`。也可以用环境变量 `CODEX_MONITOR_PYTHON` 指定完整的 pythonw.exe 路径供 launch.vbs 使用；自动联动安装仍需先生成配置。直接运行也可使用：

```powershell
python app.py
```

## 使用

- 点击白色格子中央的 ＋，在弹窗里选择项目，再搜索/选择具体对话。
- 已绑定的格子显示项目、对话标题和状态：黄色为回答中，绿色为当前轮明确完成。
- 点击已绑定格子可更换；右键菜单可更换或清除。
- 悬停查看完整标题、日志状态事件时间；右上角可关闭置顶。
- 拖动窗口边框或右下角可以缩放；中间 2×2 区域始终为正方形，每个格子也保持正方形。窗口长宽比例不同时，剩余空间留空。
- 每格正文框显示对应对话最新完成的回答全文，可用鼠标滚轮或右侧滚动条查看。正文按纯文本显示，标题行保留对话名称。
- 新一轮回答中，格子变黄，正文保留上轮已完成结果，并显示“上轮结果”；本轮完成后自动替换为新回答。中断同样不会把上轮结果标成本轮完成。
- 点击项目/标题/状态区域可更换对话；正文区域用于选中文字和滚动，右键仍可更换或清除。
- 每格右上角的“打开”按钮可快速回到这个格子绑定的 Codex 对话，不发送消息。使用官方 `codex://threads/<thread-id>` 链接，需系统已关联 Codex。
- 四个选择自动保存，下次启动恢复。初始四格为空，不预先绑定你的其它对话。

配置和错误日志分别位于 `%LOCALAPPDATA%\CodexThreadMonitor\config.json` 和 `monitor.log`，不写入 Codex 自身配置。

## 随 Codex 自动打开

这是可选功能，需要先配置并安装当前用户的登录启动项：

```powershell
python configure.py
cscript //nologo setup-autostart.vbs
```

不需要管理员权限。安装后检测器立即运行，以后登录时自动启动。无窗口的检测器每 3 秒检查本机 Codex 桌面包进程，Codex 从关闭到打开时自动打开四格小窗，不使用模型或网络。

- 首次安装后检测器立即生效，无需重新登录；用户登录启动项为 `CodexThreadMonitorAutoStart.lnk`。
- 监视器和检测器各有单实例保护，多次启动不会生成重复窗口。
- 手动关闭小窗后，本次 Codex 运行期间不会反复弹出；下次重新打开 Codex 时会再次启动。关闭 Codex 不会强制关闭仍在阅读的小窗。
- 取消绑定：运行 `cscript //nologo setup-autostart.vbs /disable`。这会移除检测器启动项并通知当前检测器退出，保留四格选择和已打开的小窗。
- 恢复绑定：运行 `cscript //nologo setup-autostart.vbs`。
- 检查绑定：`cscript //nologo setup-autostart.vbs /check`；检查当前识别结果：`python watcher.py --check`。

检测器的标志文件与错误日志位于 `%LOCALAPPDATA%\CodexThreadMonitor\autostart.enabled` 和 `watcher.log`。如果在 Windows 启动应用设置中禁用该启动项，检测器下次登录不会运行。检测规则目前匹配这台机器安装的 Windows Store Codex 包，未来如果应用更名或改用其它分发形式，需要更新规则。

对话链接参考：[官方 Commands / Deep links 文档](https://learn.chatgpt.com/docs/reference/commands#deep-links)。

## 状态的含义与限制

每秒读取所选对话的新日志，每五秒刷新本机对话目录。只读访问 `CODEX_HOME`（默认用户目录下 `.codex`）的 `state_5.sqlite` 和会话日志，不发送消息、不使用模型额度、不启动新的 Codex 回答。

项目名称和绑定关系会参考 `.codex-global-state.json` 的本地项目元数据。回答正文只在本机内存中显示，不上传，也不写入工具的配置文件。

不调用模型、不消耗模型额度、不读取 API Key。请勿上传你的会话日志或个人 config.json；仓库仅包含工具代码、说明与合成测试数据。

首版只列本机未归档主对话，不显示子代理。项目归属缺失时会按相同工作目录匹配项目名称，实在无法匹配则使用文件夹名。

完成状态基于当前轮 `task_complete`，中断显示“已中断”，缺日志或不兼容时显示“状态未知”。两者使用中性灰色而非绿色。较大的日志初次读取时会短暂显示未知，读取完成后再显示状态。

这是本地数据适配器，**依赖 Codex 当前版本的内部数据库与日志格式**。版本升级可能需要更新适配器；没有日志或旧格式缺少状态事件时不能判断。Codex 异常退出且没有记录结束时，最后一次“回答中”事件可能残留，不应把颜色当作进程健康保证；悬停时间可供核对。

绿色表示这一轮回答结束，不代表代码通过测试、任务目标达成，也不代表用户已阅读。新一轮开始时会重新变黄。

如果窗口没出现，可在本目录运行 `python app.py` 查看启动错误。`launch.vbs` 依赖 Windows Script Host；若企业策略禁用，使用上述 Python 命令。

## 验证

```powershell
python -m unittest discover -s tests -v
python app.py --check
```

测试使用临时 SQLite、临时 JSONL 和真实 Tk 组件，不修改实际 Codex 对话。`--check` 只报告项目、对话和状态数量，不输出对话正文。

当前包含 **34 项测试**，覆盖事件轮次、最新回答、日志重写、只读目录、配置、窗口缩放、按钮跳转、自动联动和单实例保护。跳转测试替代了系统调用，不会打开其它对话。

## 项目结构

```text
app.py                  四格窗口、选择器与后台读取
core.py                 只读数据目录、日志状态机与配置
navigation.py           Codex 对话深链
autostart.py             Windows 进程识别与单实例保护
watcher.py               自动启动检测器
configure.py             生成本机 Python 启动配置
launch.vbs               无控制台启动入口
setup-autostart.vbs      自动联动安装与取消
tests/                   自动测试
```

## 常见问题

**某个对话不在列表中？** 确认它属于当前用户的本机 Codex、尚未归档且日志存在。自定义数据目录需设置 `CODEX_HOME`。

**“打开”按钮无反应？** 检查 Codex 是否已安装并注册 `codex://` 协议。

**移动了项目目录？** 若已开启自动联动，先在原目录取消，再从新目录配置并安装，避免启动项仍指向旧路径。

**自动联动没有生效？** 查看 `python watcher.py --check` 的输出、Windows 启动应用设置和 `watcher.log`。目前仅识别 Windows Store Codex 桌面包。
