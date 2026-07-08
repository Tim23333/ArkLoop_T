<!-- markdownlint-disable MD033 MD041 -->
<div align="center">

<img src="./makima.jpg" alt="ArkLoop" width="160" />

# ArkLoop

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-blue" />
  <img alt="MaaFramework" src="https://img.shields.io/badge/MaaFramework-powered-7C3AED" />
  <img alt="PyWebview" src="https://img.shields.io/badge/UI-PyWebview%20%2B%20React-3B82F6" />
  <br>
  <a href="./LICENSE"><img alt="License" src="https://img.shields.io/github/license/Coodist/ArkLoop" /></a>
  <a href="https://github.com/Coodist/ArkLoop/stargazers"><img alt="Stars" src="https://img.shields.io/github/stars/Coodist/ArkLoop?style=social" /></a>
</p>

明日方舟 MuMu12 模拟器帧级作战轴录制与回放工具<br>
支持录制操作轴、自动执行轴、续录、轴编辑与截图辅助<br>
<a href="https://github.com/Coodist/ArkLoop" target="_blank"><b>🔗 本项目 GitHub 仓库</b></a><br>
如果这个项目对你有帮助，欢迎在仓库右上角点个 Star ⭐

</div>

---

> 📖 **详细使用说明请查看 [HOWTOUSE.md](./HOWTOUSE.md)**
>
> 本篇 README 只包含项目概览、开发构建与鸣谢信息。

## 功能

- **录制轴**：在 MuMu12 中实时录制部署 / 技能 / 撤退操作，自动生成时间轴
- **执行轴**：加载已有时间轴，按帧级精度自动复刻操作
- **续录轴**：执行或暂停后可继续录制，方便分段摸索
- **轴编辑**：可视化时间轴编辑器，支持新建、编辑、删除、拖动操作节点
- **截图辅助**：选中轴并进图后可截取带坐标标注的游戏画面
- **循环凹图**：配合断点与重试逻辑反复执行同一轴

## 快速开始

```powershell
# 克隆仓库
git clone https://github.com/Coodist/ArkLoop.git
cd ArkLoop

# 安装 Python 依赖
.venv\Scripts\python -m pip install -r requirements.txt

# （可选）GPU 加速头像模板匹配 —— 需 NVIDIA 显卡
# 不装则用 CPU 匹配（结果一致，仅速度差异）。装后 EXE 体积会从 ~540MB 涨到 ~4.9GB。
# .venv\Scripts\python -m pip install -r requirements-gpu.txt

# 构建前端
cd ui
npm install
npm run build
cd ..

# 运行桌面端
.venv\Scripts\python scripts\arkloop_webview.py
```

打包为独立 EXE：

```powershell
powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
```

输出位置：

```text
dist\ArkLoop\ArkLoop.exe
```

## 项目结构

```text
scripts/                正式入口
  arkloop_webview.py    PyWebview + React 桌面端主入口
  run.py                旧版 CLI 回放入口
src/                    核心逻辑
  desktop/              PyWebview API 服务：配置、时间轴、资源、状态发布
  mumu/                 MuMu 模拟器连接、截图、输入注入
  frame/                实时帧源
  logic/                坐标投影、动作执行、WebSocket 时间源
  axis/                 时间轴执行器、JSON 加载
  maa/                  MAA 识别相关配置与节点
recorder/               实时录制后端与动作识别
ui/                     React + Vite + Tailwind 时间轴编辑器
test_scripts/           开发调试脚本
tools/                  资源同步与预处理脚本
resource/               游戏数据：头像、地图、干员/关卡映射表
timelines/              用户时间轴（运行时生成，已 gitignore）
```

## 日志

运行 `ArkLoop.exe` 或 `scripts\arkloop_webview.py` 时，日志会输出到伴随的黑色命令行窗口中。调试截图与 ROI 图片默认写入 `debug/` 目录（已 gitignore）。

## 鸣谢

### 依赖

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 图像识别自动化框架
- [PyWebview](https://pywebview.flowrl.com/) — 桌面 WebView 容器
- [React](https://react.dev/) / [Vite](https://vitejs.dev/) / [Tailwind CSS](https://tailwindcss.com/) — 前端编辑器
- [Python](https://www.python.org/) / [OpenCV](https://opencv.org/) / [NumPy](https://numpy.org/) / [Pillow](https://python-pillow.org/)

### 参考项目

- [yuanyan3060/Arknights-Tile-Pos](https://github.com/yuanyan3060/Arknights-Tile-Pos) — 地图坐标数据
- [yuanyan3060/ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource) — 干员头像等游戏资源
- [Windsland52/ArknightsAutoOperator](https://github.com/Windsland52/ArknightsAutoOperator) — 帧级自动操作参考
- [MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) — 地图数据（`Arknights-Tile-Pos`）与粗流程参照
- [prts-plus](https://github.com/jue-ce-zhe/prts-plus) — 帧级自动操作的执行器算法（action / 投影 / 配置）
