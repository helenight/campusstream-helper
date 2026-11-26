# CampusStream Helper

> 跨端远控一键配置助手：把 Sunshine + Moonlight + Virtual Display Driver 的 Windows 端配置，收进一个 4 步图形化向导。

![status](https://img.shields.io/badge/status-Phase%203%20%C2%B7%20wizard-blue)
![python](https://img.shields.io/badge/python-3.11%2B-3776AB)
![license](https://img.shields.io/badge/license-MIT-green)

CampusStream Helper 面向使用 **iPad、Android 平板、Android / iOS 手机** 远程控制 Windows PC 的用户，自动完成网络诊断、虚拟显示器分辨率注入、Sunshine 配置、防火墙放行与电源保活，并生成对应客户端的配对与避坑指引。

---

## 这个项目解决什么问题

手动配置 `Sunshine + Moonlight + Virtual Display Driver` 通常要经历：

- 分不清有线 / 无线 IPv4，IPv6 地址里混着 `fe80::` 和临时地址；
- 校园网开了 AP 隔离，平板能上网却连不到宿舍 PC；
- 忘了给 `sunshine.exe` 放行防火墙，Moonlight 一直转圈；
- 手改 `vdd_settings.xml` 加分辨率，格式错一个标签就黑屏；
- 不知道虚拟屏的永久 `{device_id}`，`output_name` 写错导致串流黑屏；
- 断开后虚拟屏仍作为扩展屏存在，常用软件窗口跑到看不见的屏幕上；
- PC 睡眠后远程连接直接断开。

本项目把这些步骤做成可视化向导：**先检测、再预览、确认后才写入，并且每一步都可回滚。**

## 功能一览

| 模块 | 能力 |
|---|---|
| **A. 环境与网络体检** | 区分有线 / 无线 IPv4；提取全局 IPv6（排除 `fe80::` 与临时地址）；校园网 AP 隔离提示；`ms_tcpip6` 绑定一键修复；`netsh advfirewall` 放行 Sunshine |
| **B. 虚拟显示器与预设** | 检测 VDD 安装；内置 iPad / Android 平板 / 手机 / iPhone 预设；支持多选与自定义分辨率；安全注入 `vdd_settings.xml`；重载驱动；`DisplaySwitch /internal` 防止窗口跑偏 |
| **C. Sunshine 自动化** | 检测安装目录与服务；从日志或 `dxgi-info.exe` 提取虚拟屏 `{device_id}`；写入 `address_family`、`output_name`、`dd_configuration_option`、`dd_config_revert_on_disconnect` 与分辨率策略；一键重启服务 |
| **D. 电源保活** | 插电永不休眠、10 分钟后自动关屏；保存原配置并支持一键还原 |
| **E. 四步向导 UI** | 环境自检 → 设备定制 → 后台执行（实时日志 / 进度 / 回滚）→ 分平台配对指引 |

## 适用人群

- 在宿舍用 iPad / Android 平板 / 手机串流自己 PC 的学生；
- 想让 PC 作为「无头主机」，日常不接显示器也能远程使用的人；
- 多次手改 XML / 配置文件失败，希望有可视化向导兜底的人；
- 愿意为设备预设库提交 PR 的社区贡献者。

## 安全边界与设计原则

- **不打包、不篡改上游源码**：本工具只做检测、引导下载和配置写入，Sunshine、Moonlight、Virtual Display Driver 仍由各自官方渠道分发。
- **最小权限**：仅在防火墙、服务、驱动重载、`powercfg` 等必要操作时要求管理员权限。
- **写入前备份**：任何配置文件修改前自动生成 `*.bak`，回滚时优先使用备份。
- **Dry-Run 模拟运行**：开启后所有写操作只记录日志，不碰系统，方便开发与演示。
- **可回滚**：配置写入、VDD 条目、电源设置与防火墙规则均提供还原路径。

## 当前开发进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目骨架、设备预设、接口定义、Git 初始化 | ✅ 已完成 |
| Phase 2 | `core/` 全部底层模块 + Dry-Run 测试 | ✅ 已完成（39 个测试） |
| Phase 3 | PySide6 四步向导 + `QThread` 后台任务 | ✅ 已完成（43 个测试） |
| Phase 4 | PyInstaller 打包、UAC manifest、`v0.1.0` Release | ⏳ 计划中 |

Phase 2 已通过 39 个单元测试，并在本机完成只读实机检测（Sunshine / VDD / 网卡 / 电源）与 Dry-Run 演练。
Phase 3 在此基础上接入四步向导：后台检测 / 执行线程、实时日志、进度条、失败回滚与分平台配对指引；现有 39 个核心测试 + 4 个离屏 UI 冒烟测试，共 43 个。

> 当前可直接运行 `main.py` 使用图形向导；首个可执行文件将在 Phase 4 随 `v0.1.0` 发布。

## 快速开始

### 普通用户（v0.1.0 发布后）

1. 打开 GitHub 的 **Releases** 页面，下载最新 `CampusStreamHelper-v0.1.0.exe`。
2. 右键 → **以管理员身份运行**。
3. 按向导完成 4 步：环境自检 → 选择客户端与机型 → 一键配置 → 按指引在平板 / 手机上配对。
4. 若配置后桌面窗口位置异常，点击向导提供的 **「切回仅电脑屏幕」**。

> 当前仓库已完成 Phase 3，但尚未发布可执行文件；请先按下方开发者方式运行源码。

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

# 5. 运行全部测试（39 个核心 + 4 个离屏 UI 冒烟）
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

> `requirements.txt` 只依赖 `PySide6-Essentials`（不含 WebEngine / Addons），可显著减小 Phase 4 的打包体积；XML 处理使用标准库。
>
> 本机 Python 3.15 已可安装 PySide6 6.12。若 Phase 4 打包时 PyInstaller 尚未适配 3.15，请用 Python 3.12 重建 `.venv` 再打包。

## 四步向导会做什么

1. **环境自检**：管理员权限、电源方案、Sunshine 状态、VDD 状态、有线 / 无线网络与 IPv6 诊断。
2. **客户端与设备定制**：选择 iPadOS / Android 平板 / Android 手机 / iPhone，勾选机型分辨率或填写自定义分辨率；勾选防火墙放行与防休眠。
3. **一键配置执行**：后台线程执行，实时滚动日志、进度条、失败项提示与一键回滚。
4. **配对与避坑指引**：展示推荐连接 IP（有线优先）与 Sunshine 配对页入口，并按平台给出 Moonlight 设置清单。

## 项目结构

```text
streamhelper/
├─ assets/
│  └─ device_presets.json      # 全平台机型预设（社区可 PR 扩充）
├─ core/
│  ├─ __init__.py              # 版本号
│  ├─ common.py                # CommandRunner / Dry-Run / .bak 备份
│  ├─ privileges.py            # UAC 检测与提权
│  ├─ network.py               # 模块 A：网络、IPv6、防火墙
│  ├─ vdd.py                   # 模块 B：VDD 检测、预设、XML 注入
│  ├─ sunshine.py              # 模块 C：Sunshine 检测与配置
│  ├─ power.py                 # 模块 D：电源保活
│  └─ plan.py                  # SetupPlan / StepOutcome / SetupSummary
├─ ui/
│  ├─ __init__.py
│  ├─ wizard.py                # 四步主窗口、导航与提权
│  ├─ workers.py               # QThread 检测 / 执行 / 回滚 / 单项修复
│  ├─ styles.py                # 深色侧栏 + 卡片式 QSS
│  ├─ widgets.py               # Card / StatusPill / InfoRow / Banner
│  └─ steps/
│     ├─ step_environment.py   # Step 1：环境自检
│     ├─ step_devices.py       # Step 2：平台与分辨率
│     ├─ step_execute.py       # Step 3：执行日志与回滚
│     └─ step_guide.py         # Step 4：配对与分平台指引
├─ tests/
│  ├─ __init__.py
│  ├─ fakes.py                 # 测试用假 Runner
│  ├─ test_common.py           # Dry-Run / 备份 / 原子写入
│  ├─ test_device_presets.py   # 预设数据校验
│  ├─ test_network.py          # 网络解析 / IPv6 / 防火墙
│  ├─ test_power.py            # 电源读取 / 应用 / 还原
│  ├─ test_sunshine.py         # 配置合并 / device_id
│  ├─ test_vdd.py              # XML 注入 / 回滚
│  └─ test_ui_smoke.py         # 离屏 UI 冒烟（不弹窗、不写系统）
├─ main.py                     # 程序入口（GUI + UAC 提示 + --dry-run）
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

Virtual Display Driver 不包含在 Windows 中，需要单独安装。常见默认路径为 `C:\VirtualDisplayDriver\vdd_settings.xml`；若安装在别处，请在向导中手动选择。

### 5. Moonlight 一直显示 offline？

确认 PC 与客户端在同一网络、Sunshine 服务已启动、防火墙已放行；Moonlight 手动添加主机时填写向导推荐的 IP。若使用 IPv6，地址需要加方括号。

### 6. 配置完成后，桌面窗口跑到看不见的屏幕？

虚拟屏可能仍作为扩展屏存在。使用向导的 **「切回仅电脑屏幕」**，或按 `Win + P` 选择「仅电脑屏幕」。串流开始时 Sunshine 会按配置重新激活虚拟屏。

### 7. 会不会被杀毒软件报毒？

PyInstaller 单文件程序未做代码签名时，可能被启发式引擎误报。你可以直接运行源码，或按仓库脚本自行构建。项目不包含任何联网上传、挖矿或静默驻留行为。

### 8. 支持同时连接平板和手机吗？

支持。向导可一次注入多个分辨率；多设备场景建议选择「跟随客户端请求分辨率与刷新率自动切换」。单设备默认「不改变分辨率」，以保住 Windows 缩放比例。

### 9. 支持 macOS / Linux / 电视盒子吗？

当前只支持 **Windows PC 作为被控端**。客户端可以是 iPadOS、Android、iOS；macOS 等平台的 Sunshine 配置不在本项目范围内。

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
