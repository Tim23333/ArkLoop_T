<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img src="./makima.jpg" alt="ArkLoop" width="220" />

# ArkLoop

面向《明日方舟》与 MuMu 模拟器 12 的作战时间轴录制、编辑与精确回放工具

<p>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" />
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4?logo=windows&logoColor=white" />
  <img alt="MaaFramework" src="https://img.shields.io/badge/MaaFramework-powered-6C5CE7" />
  <img alt="React" src="https://img.shields.io/badge/UI-PyWebview%20%2B%20React-149ECA?logo=react&logoColor=white" />
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/github/license/Tim23333/ArkLoop_T" /></a>
</p>

[使用说明](./HOWTOUSE.md) · [当前架构](./docs/current-architecture.md) · [源码仓库](https://github.com/Tim23333/ArkLoop_T)

</div>

## 项目简介

前置需求: https://github.com/Tim23333/Arknights_timer

ArkLoop 将一次作战拆成带有绝对帧号的部署、技能和撤退动作，并提供从实时录制、可视化编辑到自动回放的完整工作流。

当前版本只有一个正式入口：`scripts/arkloop_webview.py`。桌面端由 PyWebview 承载 React 时间轴编辑器；Python 后端负责 MuMu 画面采集、输入监听、动作识别、坐标换算和精确回放。

回放时间统一来自外部 WebSocket 游戏时间服务提供的 `frame_count`。旧费用条校准、费用条帧数推断、离线视频识别、Excel 执行和旧 CLI 已不再属于当前运行架构。

## 主要功能

- **实时录制**：监听 MuMu 中的操作，识别部署、技能、撤退并写入绝对帧号。
- **精确回放**：在动作前预选，接近目标帧后进入暂停与逐帧控制，在暂停状态完成输入并确认恢复。
- **统一播放控制**：`PlaybackController` 集中管理等待、暂停、逐帧、动作执行、恢复、断点和停止。
- **可视化编辑**：按部署、技能、撤退三条轨道显示动作，支持新建、编辑、删除和拖动调整帧数。
- **分段续作**：暂停后保留当前帧与已部署状态，可继续回放或从当前位置继续录制。
- **时间轴管理**：支持新建、复制、重命名、导入、导出、固定、预设和断点保存。
- **地图辅助**：读取关卡地图与干员资源，提供格子坐标截图和手动动作补充。

## 运行要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11 64 位 |
| 模拟器 | MuMu 模拟器 12，建议使用 1280×720、DirectX 渲染 |
| Python | 推荐 3.11，使用项目内 `.venv` |
| Node.js | 用于构建 React 前端，建议 18 或更高版本 |
| 游戏时间源 | 外部 WebSocket 服务，必须持续推送绝对 `frame_count` |

> ArkLoop 仓库不包含游戏内存时间服务。时间服务未连接时可以浏览和编辑时间轴，但无法正常录制或精确回放。

时间服务消息格式：

```json
{
  "game_time": 12.345,
  "frame_count": 185,
  "connected": true
}
```

- `frame_count`：从本次战斗开始计算的绝对逻辑帧，录制与回放的唯一调度依据。
- `game_time`：用于界面显示的游戏时间。
- `connected`：时间服务对游戏数据的读取状态。

默认地址为 `ws://127.0.0.1:59555`，可在应用设置或 `config.json` 的 `time_source.ws_url` 中修改。

## 快速开始

### 1. 获取源码

```powershell
git clone https://github.com/Tim23333/ArkLoop_T.git
cd ArkLoop_T
```

### 2. 创建 Python 环境

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

可选的 NVIDIA GPU 头像匹配依赖：

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

不安装 GPU 依赖时会使用 CPU 路径，不影响时间轴格式和功能。源码模式会自动检测当前虚拟环境中的 CUDA Torch；打包版则使用独立依赖目录，不会把 Torch 放进主程序。

### 3. 构建前端

```powershell
Set-Location ui
npm install
npm run build
Set-Location ..
```

### 4. 准备配置

```powershell
Copy-Item config.example.json config.json
```

启动后在设置中确认以下内容：

- MuMu 安装目录、实例编号、父窗口名与渲染子窗口名。
- 外部游戏时间服务的 WebSocket 地址及连接状态。
- MuMu 已启动，并使用与时间轴关卡一致的地图和队伍。

### 5. 启动桌面端

```powershell
.venv\Scripts\python.exe scripts\arkloop_webview.py
```

首次使用可参考 [HOWTOUSE.md](./HOWTOUSE.md) 中的 MuMu 配置流程和操作注意事项。

## 基本工作流

### 录制时间轴

1. 启动 MuMu、游戏时间服务和 ArkLoop。
2. 新建或选中时间轴，确认关卡配置正确。
3. 点击录制，在 MuMu 中执行部署、技能和撤退。
4. 停止录制后检查识别结果，必要时在编辑器中修正干员、位置、方向或帧数。

### 回放时间轴

1. 进入对应关卡并准备好时间轴所需队伍。
2. 确认界面显示时间服务已连接。
3. 选中时间轴并开始播放。
4. 控制器会根据每个动作的绝对帧完成预选、精确暂停、动作输入和恢复。

### 分段续录

1. 在回放或录制过程中暂停。
2. ArkLoop 保存当前绝对帧和识别状态。
3. 选择继续播放，或从当前帧开始继续录制后续动作。

录制期间不要移动或缩放 MuMu 窗口；部署时尽量将鼠标拖到目标格中心。更完整的操作注意事项见 [HOWTOUSE.md](./HOWTOUSE.md)。

## 时间轴格式

新时间轴使用绝对 `frame`，可执行动作仅包含 `部署`、`技能`、`撤退`：

```json
{
  "settings": {
    "map_code": "1-7",
    "breakpoints": [900]
  },
  "actions": [
    {
      "frame": 120,
      "action_type": "部署",
      "oper": "斑点",
      "pos": "C3",
      "direction": "右"
    },
    {
      "frame": 600,
      "action_type": "技能",
      "oper": "斑点",
      "pos": "C3",
      "direction": "无"
    },
    {
      "frame": 840,
      "action_type": "撤退",
      "oper": "斑点",
      "pos": "C3",
      "direction": "无"
    }
  ]
}
```

旧文件中的 `cycle` / `tick` 仍可在加载时转换为 `frame`，`max_tick` 仅为旧格式兼容项。新录制文件不会写入 `bullet_threshold` 或 `frame_threshold`，精确回放参数由程序自身配置。

## 架构概览

```text
ui/src
  React 时间轴编辑器
      │ pywebview API / backend events
      ▼
scripts/arkloop_webview.py
  桌面端组合入口与运行生命周期
      ├─ recorder/backend.py
      │    实时输入、画面与动作识别
      ├─ src/axis/axis_runner.py
      │    选择并准备下一个时间轴动作
      ├─ src/axis/playback_controller.py
      │    暂停、逐帧、执行、恢复、断点与停止
      ├─ src/desktop/
      │    配置、资源、时间轴文件与状态发布
      ├─ src/mumu/ + src/maa/
      │    MuMu 输入/截图与图像识别
      └─ src/logic/ws_time_source.py
           绝对 frame_count 时间源
```

核心目录：

| 路径 | 职责 |
| --- | --- |
| `scripts/arkloop_webview.py` | 唯一正式桌面入口、API 暴露与运行生命周期 |
| `ui/src/` | React 时间轴编辑器与后端桥接 |
| `recorder/` | 实时动作录制、识别与时间轴生成 |
| `src/axis/` | JSON 加载、动作调度与集中式播放控制 |
| `src/desktop/` | 配置、资源、时间轴和前端状态服务 |
| `src/logic/` | 动作模型、坐标逻辑、输入执行与 WS 时间读取 |
| `src/mumu/` | MuMu 窗口连接、截图和输入注入 |
| `src/maa/` | MaaFramework 识别节点与适配层 |
| `resource/` | 地图、干员头像及映射数据 |
| `timelines/` | 用户时间轴与本地预设数据 |

详细边界见 [docs/current-architecture.md](./docs/current-architecture.md) 和 [docs/recording-pipeline.md](./docs/recording-pipeline.md)。

## 构建与测试

运行 Python 正式测试集：

```powershell
.venv\Scripts\python.exe -m pip install pytest
.venv\Scripts\python.exe -m pytest tests -q
```

检查前端构建：

```powershell
Set-Location ui
npm run build
```

打包独立程序：

```powershell
powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
```

该命令默认只重建 CPU 主程序，并保留 `dist\ArkLoop\dependencies` 中已安装的 GPU 依赖和现有安装器。需要同时发布新版依赖安装器时使用：

```powershell
powershell -ExecutionPolicy Bypass -File build_arkloop.ps1 -BuildInstaller
```

输出目录：

- `dist\ArkLoop\ArkLoop.exe`：默认 CPU 识别的主程序，不包含 Torch/CUDA。
- `dist\ArkLoop\ArkLoopDependencyInstaller.exe`：可选依赖安装器。

打包版用户首次运行依赖安装器时可以选择：

- **仅使用 CPU**：只写入 CPU 模式设置，不下载任何额外依赖。
- **使用 GPU 加速**：先验证当前目录中的 CUDA 12.1 版 PyTorch；版本和文件完整时直接复用，不重复下载，否则才进入下载或修复安装。

Torch wheel 下载到 `dependencies\downloads`，支持断线续传。网络中断时保留 `.part` 文件，下次运行安装器会从已有进度继续；安装成功后自动删除下载文件。

主程序启动时读取 `dependencies\mode.json`。普通构建会把启动模式重置为 CPU，但不会删除已安装的 GPU 文件；可以在主页面点击 CPU/GPU 模式按钮进行运行时切换。GPU 安装缺失、损坏或 CUDA 不可用时会记录警告并回退到 CPU，不影响时间轴的其他功能。

## 当前架构边界

以下功能已经删除，不应作为当前使用方式或新代码依赖：

- 费用条校准与费用像素帧数推断。
- 离线视频扫描和离线时间轴生成。
- 旧校准覆盖层与离线暂停检测。
- Excel/JSON 执行 CLI 与 Excel COM 执行层。
- 在时间轴 JSON 中覆盖精确回放阈值。

## 日志与问题反馈

源码模式会输出控制台日志；打包版不会打开 CMD 窗口，日志写入 `ArkLoop.exe` 同目录下的 `logs\arkloop.log`，单个文件最大 5 MB，并保留 3 份轮转记录。出现无法录制、动作帧偏移、部署失败或暂停状态异常时，请保留：

- 完整控制台日志。
- 对应时间轴 JSON。
- 问题发生前后的 MuMu 与 ArkLoop 截图。
- 可重复触发问题的操作步骤。

## 鸣谢

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework)：图像识别与自动化框架。
- [yuanyan3060/Arknights-Tile-Pos](https://github.com/yuanyan3060/Arknights-Tile-Pos)：地图坐标数据。
- [yuanyan3060/ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource)：干员头像等游戏资源。
- [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights)：地图与战斗数据参考。
- [Windsland52/ArknightsAutoOperator](https://github.com/Windsland52/ArknightsAutoOperator)：自动操作实现参考。
- [prts-plus](https://github.com/jue-ce-zhe/prts-plus)：动作模型、坐标投影和执行器设计参考。

## 许可证与声明

本项目使用 [AGPL-3.0](./LICENSE) 许可证。

ArkLoop 是非官方社区工具，与 Hypergryph、Yostar、MuMu 模拟器及上述开源项目的维护团队不存在隶属关系。游戏名称、角色和资源的相关权利归其各自权利人所有。
