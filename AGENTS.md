# KVision Windows 中文版 —— 项目状态

给下一个接手的人（或下一轮会话）看的。**开工前先读完这个文件**，别从零猜。

- 上游：`Arkanista/KVision`（Linux 优先，Qt 5.15 + QML + FFmpeg + 海康 SDK）
- 本仓库：`kunxiang1/KVision`（fork），工作分支 `master`
- 发布页：https://github.com/kunxiang1/KVision/releases/tag/windows-latest
- 目标：在 Windows 上编译出能用的海康 NVR 预览/回放客户端，操作路径贴近官方 iVMS-4200

## 状态（2026-10-28 跟进上游）

- 上游 `master` 已到 `796ac19`（v2.7.2 + HW 解码回退/Auto-Sub 码流/QML 错误日志/LNG_00549）。PR #6 中文翻译已合并。
- 本仓库 `master` = 上游 `796ac19` + 1 个 commit（`.github/` 两个文件 + 本文件），历史已压平；旧 40 个 commit 在本地分支 `backup/pre-upstream-796ac19`。
- 上游吃掉了我方 5 个补丁，`apply_patches.py` 里已删：`AVDictionary**` deleter(9)、Auto 码流 `secondaryUrl` 门闸(11)、硬解失败回退(15)、QML 硬错误日志(18)、波兰语硬编码标签(20)。上游的回退版本还多了 `m_hwOutput.reset()`，比我们原来那版更完整。
- 分支 `ci/windows-build`（上游 master + 2 个真实提交：MSVC 可编译 + CI workflow）已开 PR `Arkanista/KVision#7`。这条分支**不用**补丁脚本，源码直接改。以后上游合并了就把它并回 `master`，`apply_patches.py` 相应再删。
- 构建验证：master 与 ci/windows-build 两个 workflow run 都是 success（126MB zip）。
- 注意：带 `workflow_dispatch` 触发会走 publish，覆盖 `windows-latest` 资产 —— 拿新版验完记得用 master 再 dispatch 一次恢复完整版。

## 构建与发布

- **本机不编译**。推 `master` → GitHub Actions `windows-2022` 编译 → 下载 artifact 验货。
- 推代码 **只编译不出包**；要出包得手动触发：Actions → Build Windows → Run workflow。
- 出包走 `softprops/action-gh-release@v2`，固定 tag `windows-latest`，资产原地替换，下载链接不变。
- 源码改动**不在仓库的 src/ 里**，全在 `.github/patches/apply_patches.py`（幂等的文本补丁，CI 每次从干净 clone 打一遍）。
  - 改行为 = 改这个脚本，不是改 `src/`。
  - 脚本跑完会在 `src/` 生成/修改文件，`.github/.patched` 是幂等标记（重跑前先删）。
  - 本地验证：`rm -f .github/.patched && python .github/patches/apply_patches.py`，然后 `git checkout -- src/` 清掉。

## 已经做完的

1. **Windows 可构建**：aqtinstall 装 Qt 5.15.2 msvc2019_64、FFmpeg 8.1 shared、dumpbin 从海康 DLL 造 `.lib`（`.def` 首行必须写 `LIBRARY xxx.dll`，否则导入名变成 `xxx.dll.dll`）、GUI 子系统无黑窗。
2. **完整中文化**：`translations/kvision_zh_CN.ts` 481 条，按安防术语（摄像机/录像机/主码流/子码流/窗格/时间轴）。已作为 PR #6 回推上游。
3. **D3D11VA 硬解**：设置页勾选框 → `opts["hwaccel"]="d3d11va"`；解码失败回退软解不黑屏。**实测 32 路 26% → 20% CPU**。
4. **16x 回放**：`hikvisionarchiveplayer.cpp` 步进改 `absSpeed/2`（上限 4），UI 倍速 `[1,2,4,8,16]`。
5. **左侧常驻设备树** `src/CameraTreeDock.qml`：录像机 → 通道，双击放进焦点窗格；顶部下拉切录像机。
6. **配色**：设置页 29 个 CheckBox 补 `windowText` 白 / `base·light·mid·midlight` 深色（勾用 `palette.text` 青），否则深色主题下黑字黑勾看不清。

## 关键机制（别再重新发明）

- **顶部那排「2厂1楼 / 2厂2楼」不是切换控件**，是 `layoutsCollectionModel` 里每个录像机各存一套布局（`isNvr` + `nvrIp`），点了就是 `stackLayout.currentIndex = i`。左树切录像机复用这条路（`recorderPicked` 信号 → 找 `nvrIp` 匹配的布局）。
- 改某个窗格的 `vp.url` **不会**让画面切换，只有切布局才行。
- 独立 QML 文件必须自己 import：`Utils` 在 `CCTV_Viewer.Utils 1.0`，`Context` 在 `CCTV_Viewer.Core 1.0`。漏了就是 `ReferenceError`，而且组件静默失效。
- 配置存 `QSettings`；`Settings` 里的属性**会持久化并覆盖默认值**（踩过：`sdkDirectTile` 读回旧的 -1，改了默认值没用）。

## 已放弃的路线（别重启，除非有新理由）

- **PlayM4 原生子窗口直绘**（`src/sdkdirectpreview.*`）：六版构建没出过一帧。`root.index` 在组件里恒为 `-1`，`restart()` 从未被调用；且它一旦"接管"就会在 `Player.qml:365` 把两路 FFmpeg 都掐掉 → 稳定黑屏。硬解已经拿到主要收益（26→20%），不值得再投。
- **换 MDK SDK / libmpv**：收益上限是"再省几个点"，代价是重写播放后端 + 25 项授权（MDK 无 key 会盖二维码）。不划算。
- **给官方 iVMS-4200 瘦身**：899MB 里 `log/`、`Nginx/`、`Video.C`、`Player/` 都不能动，且它慢在 15 个进程的组件化通信，删文件一秒都不会快。

## 已知限制

- 回放与预览同时开会流量叠加。
- 1 路时流量比 32 路还大（疑似切布局后旧播放器未销毁），未修。
- 状态悬浮框里 CPU/内存显示 0.0（SDK 状态查询没接）。

## 调试铁律（这次踩出来的）

1. **报"没反应 / 黑屏"，先读日志再动代码**：
   `kvision.exe 2> log.txt` → `findstr /c:"..." log.txt`
2. QML 的 `console.log` 映射到 qDebug，会被 `main.cpp` 的日志过滤器吞掉；**探针用 `console.warn`**（qWarning）。
3. 改 `if` 之前，把那个函数的**完整函数体**读完 —— 尤其是自己早先写的补丁（`Player.qml:365` 就是这么黑的）。
4. 补丁跑完，**在生成的文件上 grep 或 assert 验一遍再推**。文本补丁打在模板字符串上而不是产物上，静默失效很难发现。
5. JS 里 `"a" + b === c` 因 `+` 优先级高于 `===`，比较的是整串，会打出裸 `false`。

## 关键 ID

- 上游 PR：`Arkanista/KVision#6`（中文翻译）
- Release：`425159354`，tag `windows-latest`
- 可用的验证包：`D:\tmp\kvrun24`（= 回滚后的 master 树）
