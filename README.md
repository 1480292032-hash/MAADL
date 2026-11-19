<div align="center">

# MAADL

**《约战：精灵再临》怀旧版 日常与推图助手**

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 图像识别自动化框架 · 纯 JSON 流水线 · 开箱即用

</div>

---

## ✨ 功能

| 功能 | 状态 | 说明 |
| ---- | ---- | ---- |
| 启动游戏 | ✅ 可用 | 启动游戏、关闭登录/公告弹窗、确认进入主界面 |
| 剧情关卡推图 | 🚧 开发中 | 自动战斗推剧情关卡，打完自动进下一关，体力不足自动停止 |
| 清日常 | 🚧 骨架 | 签到 / 邮件 / 在线奖励 / 每日副本（逐步补充中） |

> 「🚧」表示流程框架已就绪，等待补充游戏界面识别素材，欢迎提 Issue / PR 一起完善！

## 🖥️ 运行环境

- Windows 10/11（macOS / Linux 版本随 Release 一同提供，未经充分测试）
- 安卓模拟器：雷电 / UU / MuMu 等均可，或安卓真机
- **模拟器分辨率必须设置为 1280x720（16:9，DPI 240）**
- 模拟器开启 ADB 调试

## 📦 下载使用

1. 前往 [Releases](https://github.com/1480292032-hash/MAADL/releases) 页面，下载 `MAADL-win-x86_64` 压缩包
2. 解压到任意**不含中文和空格**的目录（建议）
3. 双击运行 `MFAAvalonia.exe`
4. 控制器选择 **安卓模拟器 / 设备**：
   - 雷电模拟器默认地址 `127.0.0.1:5555`
   - UU 模拟器请在设置/问题诊断页查看 ADB 端口
   - 也可点击「自动检测」让程序寻找已连接的模拟器
5. 勾选任务（如「启动游戏」+「剧情关卡推图」），点击开始

### 游戏包名配置（首次使用）

当前适配的是**官服怀旧版**（[datealive.com/huaijiu](https://datealive.com/huaijiu)），包名 `com.datealive.huaijiu.hta`，开箱即用。

使用其他渠道包（如正常版 B服 `com.heitao.yhdzz.bilibili`）时，界面不同，需自行适配；可先用 ADB 查包名并替换 `assets/resource/pipeline/startup.json` 中 `"游戏启动"` 节点的 `"package"` 字段：

```bash
adb connect 127.0.0.1:5555   # 端口按模拟器实际修改
adb shell pm list packages -3
```

## 🛠️ 参与开发

本项目使用官方模板 [MaaPracticeBoilerplate](https://github.com/MaaXYZ/MaaPracticeBoilerplate) 构建，欢迎 PR！

### 环境准备

```bash
# 1. 安装 Python 3.9+ 与 VSCode 插件「Maa Pipeline Support」（nekosu.maa-support）
# 2. 克隆项目（含子模块）
git clone --recurse-submodules https://github.com/1480292032-hash/MAADL.git
# 3. 配置本地 OCR 模型（从子模块复制，不上传）
python tools/configure.py
# 4. 安装调试工具
pip install MaaFw MaaDebugger
```

### 调试

用 VSCode 打开项目根目录（已自动关联 pipeline / interface 的 JSON Schema），使用 **Maa Pipeline Support** 插件：

1. 连接模拟器（插件内填 ADB 地址）
2. 截图 → 框选 ROI → 裁剪模板图，保存到 `assets/resource/image/` 对应子目录
3. 单节点运行 / 断点调试流水线

详细开发文档见 [docs/zh_cn/develop/how_to_develop.md](docs/zh_cn/develop/how_to_develop.md)。

### 发布

修改代码后提交，然后打 tag 触发自动打包发布：

```bash
git tag v0.2.0
git push origin v0.2.0
```

GitHub Actions 会自动构建包含 MFAAvalonia 通用 GUI 的完整发布包。

## 🧭 目录结构

```
MAADL/
├── assets/
│   ├── interface.json            # 项目接口定义（任务列表 / 选项 / 控制器）
│   └── resource/
│       ├── image/                # 模板识别图片（按界面分目录）
│       ├── model/                # OCR 等模型（本地生成，不入库）
│       └── pipeline/
│           ├── common.json       # 通用节点（关弹窗 / 重连）
│           ├── startup.json      # 启动游戏
│           ├── story.json        # 剧情推图循环
│           └── daily.json        # 清日常
├── agent/                        # Python 自定义识别/动作（当前未启用）
├── docs/                         # 开发文档
└── .github/workflows/            # CI：打包 / 发布 / 校验
```

## ❓ 常见问题

**连接模拟器失败 / 截图全黑？**
确认模拟器 ADB 调试已开启、端口正确；雷电模拟器建议在设置中开启「ADB 调试」。分辨率必须是 1280x720。

**识别不到界面 / 任务中途停止？**
当前版本基于 OCR 文本识别，游戏界面文案更新可能导致识别失效。请携带 `debug/maafw.log` 和失败截图提 [Issue](https://github.com/1480292032-hash/MAADL/issues)。

**推图中途点了奇怪的东西？**
OCR 识别的误伤。欢迎反馈具体界面截图，我们会用模板图替换，提高准确率。

## 📜 免责声明

- 本项目为开源的学习交流项目，仅供个人学习、研究使用，请勿用于商业用途
- 请合理控制使用时间，遵守游戏用户协议，因使用本程序导致的账号问题由使用者自行承担
- 本项目不修改游戏数据、不注入游戏进程，仅通过图像识别模拟人工点击

## 🙏 鸣谢

本项目由 **[MaaFramework](https://github.com/MaaXYZ/MaaFramework)** 强力驱动！

通用 GUI：[MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia)

感谢以下开发者作出的贡献：

[![Contributors](https://contrib.rocks/image?repo=1480292032-hash/MAADL&max=1000)](https://github.com/1480292032-hash/MAADL/graphs/contributors)

## License

[MIT](./LICENSE)
