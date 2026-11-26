# CampusStream Helper

> 跨端远控一键配置助手：把 Sunshine + Moonlight + Virtual Display Driver 的 Windows 端配置，收进一个 4 步图形化向导。

![status](https://img.shields.io/badge/status-v0.2.1%20%C2%B7%20release-green)
![python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![license](https://img.shields.io/badge/license-MIT-green)

CampusStream Helper 面向使用 **iPad、Android 平板、Android / iOS 手机** 远程控制 Windows PC 的用户，自动完成依赖检测与安装、网络诊断、虚拟显示器分辨率注入、Sunshine 配置、防火墙放行与电源保活，并生成对应客户端的配对与避坑指引。界面支持跟随系统的深色 / 浅色主题。

---

## 这个项目解决什么问题

手动配置 `Sunshine + Moonlight + Virtual Display Driver` 通常要经历：

- 分不清有线 / 无线 IPv4，IPv6 地址里混着 `fe80::` 和临时地址；
- 校园网开了 AP 隔离，平板能上网却连不到宿舍 PC；
- 不知道 Sunshine / VDD 应该下载哪个版本，GitHub 直连还很慢；
- 忘了给 `sunshine.exe` 放行防火墙，Moonlight 一直转圈；
- 手改 `vdd_settings.xml` 加分辨率，格式错一个标签就黑屏；
- 不知道虚拟屏的永久 `{device_id}`，`output_name` 写错导致串流黑屏；
- 断开后虚拟屏仍作为扩展屏存在，常用软件窗口跑到看不见的屏幕上；
- PC 睡眠后远程连接直接断开。

本项目把这些步骤做成可视化向导：**先检测、再预览、确认后才写入，并且每一步都可回滚。**

## 功能一览

| 模块 | 能力 |
|---|---|
| **A. 环境与网络体检** | 管理员、GPU、Sunshine、VDD、网络五项逐项检测；四态徽章与实时进度；区分有线 / 无线 IPv4；提取全局 IPv6（排除 `fe80::` 与临时地址）；校园网 AP 隔离提示；`ms_tcpip6` 绑定一键修复；`netsh advfirewall` 放行 Sunshine |
| **B. 虚拟显示器与预设** | 检测 VDD 安装；内置 iPad / Android 平板 / 手机 / iPhone 预设；支持多选与自定义分辨率；安全注入 `vdd_settings.xml`；重载驱动；`DisplaySwitch /internal` 防止窗口跑偏 |
| **C. Sunshine 自动化** | 检测安装目录与服务；从日志或 `dxgi-info.exe` 提取虚拟屏 `{device_id}`；写入 `address_family`、`output_name`、`dd_configuration_option`、`dd_config_revert_on_disconnect` 与分辨率策略；一键重启服务 |
| **D. 电源保活** | 插电永不休眠、10 分钟后自动关屏；保存原配置并支持一键还原 |
| **E. 四步向导 UI** | 环境自检 → 设备定制 → 后台执行（实时日志 / 进度 / 回滚）→ 分平台配对指引 |
| **F. 依赖自动安装** | 解析 GitHub 最新 Sunshine / VDD Release，匹配 CPU 架构，校验文件大小后安装；可选 GitHub 加速代理；失败时打开浏览器手动下载或选择本地安装包 |
| **G. 深浅色主题** | 跟随系统 / 浅色 / 深色三档切换；主窗口、对话框、输入框、日志与状态徽章全部适配 |

## 适用人群

- 在宿舍用 iPad / Android 平板 / 手机串流自己 PC 的学生；
- 想让 PC 作为「无头主机」，日常不接显示器也能远程使用的人；
- 多次手改 XML / 配置文件失败，希望有可视化向导兜底的人；
- 愿意为设备预设库提交 PR 的社区贡献者。

## 安全边界与设计原则

- **不篡改上游安装包**：Sunshine、Moonlight、Virtual Display Driver 仍由各自官方 GitHub Release 分发；本工具只按 API 解析最新资产、校验文件大小并调用官方安装方式。
- **最小权限**：仅在防火墙、服务、驱动重载、`powercfg` 等必要操作时要求管理员权限；自动安装前会再次确认，非管理员运行时默认建议先 Dry-Run。
- **写入前备份**：任何配置文件修改前自动生成 `*.bak`，回滚时优先使用备份。
- **Dry-Run 模拟运行**：开启后所有写操作只记录日志，不碰系统；自动安装只解析版本、下载链接与将执行的命令，不下载、不解压、不安装。
- **可回滚**：配置写入、VDD 条目、电源设置与防火墙规则均提供还原路径。
- **下载安全**：只接受 HTTPS 资产链接，按 GitHub API 返回的大小校验；ZIP 解压拒绝绝对路径、`..` 与符号链接，并保留已有 `vdd_settings.xml`。

## 当前开发进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目骨架、设备预设、接口定义、Git 初始化 | ✅ 已完成 |
| Phase 2 | `core/` 全部底层模块 + Dry-Run 测试 | ✅ 已完成（39 个测试） |
| Phase 3 | PySide6 四步向导 + `QThread` 后台任务 | ✅ 已完成（43 个测试） |
| Phase 4 | PyInstaller 打包、UAC manifest、`v0.1.0` Release | ✅ 已完成（48 个测试） |
| v0.2.0 | 深浅色主题、Step 1 逐项检测、Sunshine / VDD 自动下载安装 | ✅ 已完成（123 个测试） |
| v0.2.1 | 修复 Step 3 虚拟屏 `device_id` 提取、执行顺序与日志诊断 | ✅ 已完成（135 个测试） |

Phase 2 已通过 39 个单元测试，并在本机完成只读实机检测（Sunshine / VDD / 网卡 / 电源）与 Dry-Run 演练。
Phase 3 在此基础上接入四步向导：后台检测 / 执行线程、实时日志、进度条、失败回滚与分平台配对指引。
Phase 4 完成单文件打包：内嵌 `asInvoker` UAC 清单与 VERSIONINFO，构建后自动做离屏冒烟测试。
v0.2.0 新增主题管理、五项环境逐项检测和依赖自动安装，测试总数达到 **123 个**（含下载安装核心、Worker 与对话框离屏测试）。
v0.2.1 修复 Step 3 在虚拟屏未激活时提取 `device_id` 失败的问题，测试总数达到 **135 个**。

### v0.2.1 修复内容

- **重排 Step 3 执行顺序**：写入 VDD XML → 重载驱动 → 等待 2 秒 → 首次重启 Sunshine 枚举显示器 → 等待 2.5 秒 → 从日志提取 `device_id` → 写入 `sunshine.conf` → 再次重启生效。
- **修复 Sunshine 日志解析**：从日志末尾向前解析 `Currently available devices:` 的 JSON 显示器列表，关联同一设备的 `device_id`、`display_name`、`friendly_name`、`info` 与 `edid`。
- **增强虚拟屏识别**：优先匹配 `VDD`、`MTT`、`Virtual`、`Idd`、`Indirect` 关键词；没有关键词时选择非主屏候选，并保留完整花括号 `{...}`。
- **增强失败诊断**：Step 3 会输出实际日志路径、最近显示器列表、格式化 JSON 与 `dxgi-info.exe` 原始输出。
- **保留现有配置**：如果本次未找到新设备，但 `sunshine.conf` 已有有效的 `output_name = {...}`，会沿用并提示，不清空原值。

> `v0.2.1` 的 Windows 单文件程序为 `CampusStreamHelper-v0.2.1.exe`；构建后会在 `dist\` 下生成，并输出 SHA256。

### v0.2.0 更新内容

- **全界面深浅色主题**：启动时读取系统主题，右上角可切换「跟随系统 / 浅色 / 深色」，切换立即生效并记住选择。
- **Step 1 逐项检测进度**：管理员、GPU、Sunshine、VDD、网络五项分别显示 `⏳ 检测中…`、`✅ 已就绪`、`⚠️ 需处理`、`❌ 检测失败`，并带进度条、完成时间与修复入口。
- **Sunshine / VDD 自动下载安装**：自动匹配 Windows AMD64 / ARM64 资产，显示下载百分比与实时速度；支持 GitHub 加速代理、取消、安装前确认、Dry-Run 与两种手动兜底。
- **安装完成后自动复检**：安装成功会重新执行 Step 1 检测，环境卡片自动刷新。

> `v0.2.0` 的 Windows 单文件程序为 `CampusStreamHelper-v0.2.0.exe`；构建后会在 `dist\` 下生成，并输出 SHA256。

## 快速开始

### 普通用户（下载 exe）

1. 打开 GitHub 的 **Releases** 页面，下载 `CampusStreamHelper-v0.2.1.exe`。
2. 可选校验：在下载目录运行
   `Get-FileHash .\CampusStreamHelper-v0.2.1.exe -Algorithm SHA256`，
   与 Release 说明中的 SHA256 对比。
3. 双击启动；程序会自己检测权限并提示提权。也可以右键 → **以管理员身份运行**。
4. 按向导完成 4 步：环境自检 → 选择客户端与机型 → 一键配置 → 按指引在平板 / 手机上配对。
5. 若 Step 1 检测到 Sunshine / VDD 缺失，点击「一键自动下载安装」；GitHub 访问慢时可在对话框填写加速代理，失败时也可手动下载或选择本地安装包。
6. 右上角可切换「跟随系统 / 浅色 / 深色」主题。
7. 若配置后桌面窗口位置异常，点击向导提供的 **「切回仅电脑屏幕」**。

> 单文件程序无需安装 Python，也不会写入注册表卸载项；运行状态、日志与回滚清单保存在 `%LOCALAPPDATA%\CampusStreamHelper`。
>
> 未签名的 exe 首次运行可能被 SmartScreen 拦截：点「更多信息」→「仍要运行」。若仍不放心，可按下方开发者方式从源码运行。

### 开发者（当前可用）

```powershell
cd D:\ClaudeCodeworkplace\streamhelper

# 1. 创建虚拟环境（推荐 Python 3.12 / 3.13）
python -m venv .venv

# 2. 安装依赖（直接调用 venv 内的 python，避免 PowerShell 执行策略问题）
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt

# 3. 启动图形向导（管理员运行时可直接真实配置）
.\.venv\Scripts\python.exe main.py

# 4. 或只检测、不写入：Dry-Run 模式
.\.venv\Scripts\python.exe main.py --dry-run

# 5. 运行全部测试（当前 135 个）
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Remove-Item Env:\QT_QPA_PLATFORM

# 6. 打包单文件 exe（自动跑测试 → 打包 → 冒烟测试 → 输出 SHA256）
.\packaging\build.ps1
```

> 打包产物：`dist\CampusStreamHelper.exe` 与带版本号的 `dist\CampusStreamHelper-v0.2.1.exe`。构建脚本会先运行全部测试，再用 `--smoke-test` 验证打包后的程序能加载设备预设并输出版本号。
>
> `requirements.txt` 只依赖 `PySide6-Essentials`（不含 WebEngine / Addons）；XML、ZIP 与下载逻辑均使用标准库。本机 v0.2.1 单文件约 47.0 MB，构建完成后终端会打印实际体积与 SHA256。

## 发布流程（维护者）

```powershell
# 1. 版本号：同步修改 core/__init__.py、packaging/version_info.txt、
#    packaging/app.manifest，并跑测试

# 2. 打包（自动测试 + 冒烟测试，输出体积与 SHA256）
.\packaging\build.ps1

# 3. 本地验收（会打开界面，建议先看是否显示 v0.2.1）
.\dist\CampusStreamHelper-v0.2.1.exe --dry-run

# 4. 提交源码（dist/、build/ 与 *.bak 已被 .gitignore 排除）
git add README.md core packaging tests
git commit -m "build: 发布 v0.2.1"

# 5. 合并修复分支到 main
git switch main
git pull --ff-only origin main
git merge --no-ff fix/sunshine-device-id -m "merge: 发布 v0.2.1"
git push origin main

# 6. 打附注标签并推送
git tag -a v0.2.1 -m "CampusStream Helper v0.2.1"
git push origin v0.2.1
```

随后在 GitHub 仓库页：**Releases → Draft a new release** → 选择标签 `v0.2.1` → 上传
`dist\CampusStreamHelper-v0.2.1.exe` → 把构建脚本输出的 SHA256 写进说明 → **Publish release**。

> `git pull --ff-only` 只允许快进合并；如果失败，说明远端 `main` 有新提交，应先查看差异再处理。
> 不要使用 `git push --force`。若标签写错，用 `git tag -d v0.2.1` 删除本地标签后重新打，再推送。

## 四步向导会做什么

1. **环境自检**：后台逐项检测管理员权限、GPU、Sunshine、VDD 与网络，显示实时进度、四态徽章、完成时间和修复按钮；检测到 Sunshine / VDD 缺失时可直接打开自动下载安装。
2. **客户端与设备定制**：选择 iPadOS / Android 平板 / Android 手机 / iPhone，勾选机型分辨率或填写自定义分辨率；勾选防火墙放行与防休眠。
3. **一键配置执行**：后台线程执行，实时滚动日志、进度条、失败项提示与一键回滚。
4. **配对与避坑指引**：展示推荐连接 IP（有线优先）与 Sunshine 配对页入口，并按平台给出 Moonlight 设置清单。

## 项目结构

```text
streamhelper/
├─ assets/
│  ├─ device_presets.json      # 全平台机型预设（社区可 PR 扩充）
│  ├─ icon.ico                 # exe / 窗口图标（多尺寸）
│  ├─ icon.png                 # README / 商店展示用
│  └─ check.svg                # 复选框 / 主题图标
├─ core/
│  ├─ __init__.py              # 版本号
│  ├─ common.py                # CommandRunner / Dry-Run / .bak 备份
│  ├─ privileges.py            # UAC 检测与提权
│  ├─ detection.py             # 五项检测模型与四态徽章
│  ├─ gpu.py                   # WMI 显卡检测
│  ├─ downloader.py            # GitHub Release 解析 / 下载 / 安装
│  ├─ network.py               # 模块 A：网络、IPv6、防火墙
│  ├─ vdd.py                   # 模块 B：VDD 检测、预设、XML 注入
│  ├─ sunshine.py              # 模块 C：Sunshine 检测与配置
│  ├─ power.py                 # 模块 D：电源保活
│  └─ plan.py                  # SetupPlan / StepOutcome / SetupSummary
├─ ui/
│  ├─ __init__.py
│  ├─ wizard.py                # 四步主窗口、导航与提权
│  ├─ theme.py                 # 深浅色主题、系统探测与 QSS
│  ├─ workers.py               # QThread 检测 / 执行 / 回滚 / 下载安装
│  ├─ widgets.py               # Card / StatusPill / InfoRow / Banner
│  ├─ dialogs/
│  │  └─ package_dialog.py     # 自动下载安装对话框
│  └─ steps/
│     ├─ step_environment.py   # Step 1：逐项环境自检
│     ├─ step_devices.py       # Step 2：平台与分辨率
│     ├─ step_execute.py       # Step 3：执行日志与回滚
│     └─ step_guide.py         # Step 4：配对与分平台指引
├─ packaging/
│  ├─ campusstream_helper.spec # PyInstaller 单文件配置
│  ├─ app.manifest             # UAC asInvoker + Win10/11 兼容性
│  ├─ version_info.txt         # exe 版本资源（与 core.__version__ 同步）
│  ├─ make_icon.py             # 重新生成 assets/icon.ico
│  └─ build.ps1                # 测试 → 打包 → 冒烟测试 → SHA256
├─ tests/
│  ├─ __init__.py
│  ├─ fakes.py                 # 测试用假 Runner
│  ├─ test_common.py           # Dry-Run / 备份 / 原子写入
│  ├─ test_device_presets.py   # 预设数据校验
│  ├─ test_detection.py        # 五项检测模型 / 汇总
│  ├─ test_gpu.py              # WMI 显卡解析
│  ├─ test_downloader.py       # 下载 / 校验 / ZIP 安全 / 安装
│  ├─ test_network.py          # 网络解析 / IPv6 / 防火墙
│  ├─ test_power.py            # 电源读取 / 应用 / 还原
│  ├─ test_sunshine.py         # 配置合并 / device_id
│  ├─ test_vdd.py              # XML 注入 / 回滚
│  ├─ test_theme.py            # 对比度 / QSS / 主题切换
│  ├─ test_step_environment.py # Step 1 进度与四态徽章
│  ├─ test_package_worker.py   # 下载安装 Worker
│  ├─ test_package_dialog.py   # 下载安装对话框
│  ├─ test_ui_smoke.py         # 离屏 UI 冒烟（不弹窗、不写系统）
│  └─ test_packaging.py        # 清单 / 版本 / 图标 / spec 资源校验
├─ main.py                     # 程序入口（GUI + 崩溃日志 + --dry-run / --smoke-test）
├─ requirements.txt
├─ requirements-dev.txt
├─ .gitignore
├─ LICENSE
└─ README.md
```

## 贡献设备预设

只需编辑 `assets/device_presets.json` 的 `devices` 数组，新增一项：

```json
{
  "id": "your-device-id",
  "platform": "android_tablet",
  "label": "品牌 型号",
  "width": 2560,
  "height": 1600,
  "refresh_options": [60, 120],
  "default_refresh_hz": 120,
  "windows_scaling_percent": 150,
  "aspect_ratio": "16:10",
  "notes": "串流时的注意事项"
}
```

字段要求：

| 字段 | 说明 |
|---|---|
| `id` | 全局唯一，小写 kebab-case |
| `platform` | `ipados` / `android_tablet` / `android_phone` / `ios` |
| `width` / `height` | 客户端屏幕物理像素，不要填 Windows 缩放后的值 |
| `refresh_options` | 该机型实际支持的刷新率数组 |
| `default_refresh_hz` | 必须出现在 `refresh_options` 中 |
| `windows_scaling_percent` | 推荐的 Windows 缩放，100~300 |
| `aspect_ratio` | 仅用于 UI 展示 |
| `notes` | 该机型的特殊注意事项 |

提交前运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

然后使用 `feat(presets): add <机型>` 作为提交信息发起 PR。

## 常见问题 FAQ

### 1. 为什么必须以管理员身份运行？

防火墙规则、Windows 服务重启、VDD 驱动重载、`powercfg` 修改都需要管理员权限。只做环境检测时可使用 Dry-Run，不写入任何系统配置。

### 2. 校园网里平板能上网，却连不到 PC？

高校校园网常启用 **AP 隔离**，同一 Wi-Fi 下的设备不能互访。优先方案是让 PC 插宿舍有线网；若必须跨网，可使用 Tailscale、星空组网、蒲公英等组网工具。若学校和 PC 都能获得全局 IPv6，也可尝试 IPv6 直连，但需在防火墙中放行 Sunshine。

### 3. iPad / 平板连上了但没有画面或黑屏？

按顺序检查：

1. Sunshine 服务是否运行；
2. `output_name` 是否与虚拟屏 `{device_id}` 一致；
3. 防火墙是否放行 `sunshine.exe`；
4. 是否在 Sunshine 中启用了「停用其他显示器，仅激活指定显示器」；
5. Moonlight 的分辨率是否为 `Custom` 且与注入的分辨率一致。

### 4. 提示找不到 `vdd_settings.xml`？

Virtual Display Driver 不包含在 Windows 中，需要单独安装。可以在 Step 1 点击「一键自动下载安装」；程序优先部署到已检测到的安装目录，未检测到时使用默认路径 `C:\VirtualDisplayDriver`。若驱动装在别处，请先用「选择本地安装包继续安装」或手动安装，再重新检测。

### 5. Moonlight 一直显示 offline？

确认 PC 与客户端在同一网络、Sunshine 服务已启动、防火墙已放行；Moonlight 手动添加主机时填写向导推荐的 IP。若使用 IPv6，地址需要加方括号。

### 6. 配置完成后，桌面窗口跑到看不见的屏幕？

虚拟屏可能仍作为扩展屏存在。使用向导的 **「切回仅电脑屏幕」**，或按 `Win + P` 选择「仅电脑屏幕」。串流开始时 Sunshine 会按配置重新激活虚拟屏。

### 7. 首次运行被 SmartScreen 或杀毒软件拦截？

程序未做代码签名，Windows SmartScreen 会提示「已保护你的电脑」：点 **更多信息 → 仍要运行**。
PyInstaller 单文件程序也可能被启发式引擎误报；你可以直接运行源码，或按仓库脚本自行构建。项目不包含任何联网上传、挖矿或静默驻留行为。
若程序异常退出，查看 `%LOCALAPPDATA%\CampusStreamHelper\crash.log`。

### 8. 支持同时连接平板和手机吗？

支持。向导可一次注入多个分辨率；多设备场景建议选择「跟随客户端请求分辨率与刷新率自动切换」。单设备默认「不改变分辨率」，以保住 Windows 缩放比例。

### 9. 支持 macOS / Linux / 电视盒子吗？

当前只支持 **Windows PC 作为被控端**。客户端可以是 iPadOS、Android、iOS；macOS 等平台的 Sunshine 配置不在本项目范围内。

### 10. 备份、回滚状态和日志保存在哪里？

- 被修改的配置文件旁：`sunshine.conf.bak.<时间戳>`、`vdd_settings.xml.bak.<时间戳>`；
- 电源与 VDD 回滚清单：`%LOCALAPPDATA%\CampusStreamHelper\`；
- 自动下载的安装包：`%LOCALAPPDATA%\CampusStreamHelper\downloads\`；
- 崩溃日志：`%LOCALAPPDATA%\CampusStreamHelper\crash.log`。

这些文件可能包含本机 IP、设备 ID，已被 `.gitignore` 排除，请勿上传到公开仓库。

### 11. 如何切换深色 / 浅色主题？

主窗口右上角有主题切换按钮，可选择「跟随系统 / 浅色 / 深色」，切换立即生效并记住上次选择。默认跟随 Windows 的「应用模式」设置；如果系统接口无法判断，会读取注册表中的浅色 / 深色偏好。

### 12. GitHub 下载很慢或自动下载失败怎么办？

可以在下载对话框的「GitHub 加速代理」中填写形如 `https://gh-proxy.com/` 的前缀，程序会用它重试 API 与下载。仍然失败时：

- 点「打开浏览器手动下载」，从官方 Releases 页面下载；
- 点「选择本地安装包继续安装」，选择已下载的 `.msi` 或 `.zip`。

下载文件会缓存在 `%LOCALAPPDATA%\CampusStreamHelper\downloads\`；大小与 GitHub API 不一致时会自动清理并重试。

### 13. 自动安装安全吗？会覆盖我的配置吗？

程序只从 GitHub Release 的 HTTPS 资产下载，按 API 返回大小校验，下载完成后再次确认才执行安装。Sunshine 使用官方 MSI 的 `msiexec /passive`；VDD 压缩包会先做 ZIP 路径安全检查，再部署到安装目录。已有 `vdd_settings.xml` 会保留，不会被压缩包内文件覆盖。

建议先开启 Dry-Run：它只显示版本、下载链接与将执行的安装命令，不会下载、解压或改动系统。

### 14. Step 1 显示「⚠️ 需处理」或「❌ 检测失败」怎么办？

- **管理员权限**：点「以管理员身份重启」，或开启 Dry-Run 仅体验流程。
- **Sunshine / VDD 未安装**：点「一键自动下载安装」。
- **网络提示**：按卡片按钮启用 IPv6 绑定、放行防火墙，或查看 AP 隔离说明。
- **GPU 检测失败**：通常是 WMI 查询异常或显卡驱动未就绪，更新驱动后点「重新检测」。
- **某项检测失败**：卡片会显示具体原因；修复后点右上角「重新检测」。

## 上游项目与致谢

本项目站在以下开源项目的肩膀上：

- **Sunshine** — Windows/Linux/macOS 游戏串流服务端  
  <https://github.com/LizardByte/Sunshine> · <https://docs.lizardbyte.dev/projects/sunshine/>
- **Moonlight** — 跨平台串流客户端  
  <https://moonlight-stream.org/> · <https://github.com/moonlight-stream>
- **Virtual Display Driver** — Windows 虚拟显示器驱动  
  <https://github.com/VirtualDrivers/Virtual-Display-Driver>

感谢 LizardByte、Moonlight Game Streaming Project 以及 Virtual Display Driver 的维护者与贡献者。本项目与上述项目官方无隶属关系，也不代表其立场。

## 许可证

本项目采用 [MIT License](LICENSE)。

## 免责声明

- 请仅远程控制你本人拥有或已获得明确授权的设备。
- 使用远程控制与组网工具时，请遵守学校 / 单位网络管理规定。
- 显示配置修改可能导致短暂黑屏或窗口位置变化；项目已提供备份与回滚，但仍建议在操作前创建系统还原点。
- 本项目按「现状」提供，不对因使用导致的设备、数据或网络问题承担责任。
