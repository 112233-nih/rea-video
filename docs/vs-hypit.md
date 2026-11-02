# rea-video vs Hypit：学什么，怎么超越

> 分析对象：[hypit-ai/hypit](https://github.com/hypit-ai/hypit) 主分支，2026-10-29 浅克隆阅读
>（README、`skills/hypit/SKILL.md` 及其 references、`packages/`）。

## 1. Hypit 是什么

- 定位："让 AI Agent 复刻任何爆款视频"。以 `/hypit` Skill 的形式运行在 Claude Code、Codex 等 agent 里。
- 核心是自定义语言 **SVML**（加 SVS 和 SVRun）：画面、字幕、B-roll、特效都锚定在**词**上，而不是秒上。最后编译成 HTML 视频，由无头 Chromium 并发渲染（示例用了 64 个进程）。
- 拉片方式：`hypit transcribe` 调用 WhisperX 做字级对齐；`hypit media tile/tiles/frames/boundaries` 生成带时间和字幕标注的帧网格，再由 agent **看图理解**。结论写进 `ANALYSIS.md`（全片解读）和 `TIMELINE.md`（时间码细节）。
- 模型接入分四层：Model（请求语义）、Provider（实现）、Endpoint（账号实例）、Runtime Profile（选择与凭据）。默认网关是 HypiHub，也支持 BYOK。
- 已有的模型包：
  - 视频：Seedance（含 2.5 / Mini）、PixVerse V6 / C1、MiniMax H3、Grok Imagine、Wan
  - 图像：GPT Image 2、Nano Banana、Seedream 5 Lite
  - 语音：ElevenLabs、Fish Audio、MiMo
  - 其他：WhisperX 对齐、火山引擎人像抠像、OpenCV 图像操作
- 示例成本：每条 1.07–1.15 美元，约 2.6 万星。
- 许可：修改版 Apache 2.0。**禁止**未经授权做多租户 SaaS、商业再分发，禁止移除 Logo；产出内容归用户。

## 2. 值得学习的设计

| 设计 | 说明 | rea-video 怎么吸收 |
|---|---|---|
| 语义时间 | 事件锚定在台词或动作上，改词后时间轴自动重排 | IR 支持多种锚点，词锚点是其中之一 |
| 组件化 | 字幕、MG、B-roll 都是可参数化组件，换主播不动字幕 | 代码层用 Remotion 组件，参数暴露进 IR |
| 参考视频"先当证据，再当输入" | 只有当动作、运镜等时间行为无法用文字和静帧描述时，才把原片片段喂给模型 | 写成路由规则（见 `routing.yaml` 的 R04） |
| 输入职责分离 | 角色参考、商品参考、动作参考、声音参考、台词，各自承担明确的职责 | Shot Recipe 中每个输入都要标注职责 |
| 文件即记忆 | BRIEF / TREATMENT / ANALYSIS / TIMELINE / PROGRESS | 采用类似约定，但核心状态放在机器可读的 IR |
| Run/Build 复用和成本计划 | 已产出的素材可以显式复用；花钱前先确认预算 | 生成单元级缓存；先估算成本，再到确认节点 |
| Studio 加时间码评论 | 用户可以在时间轴上评论和调参 | P2 之后做预览和评论，也可以直接导出剪映草稿 |
| 导演经验沉淀在 Skill 里 | 大量图像、视频、声音、字幕方面的 craft 指南 | 我们沉淀为**结构化的能力地图和规则**，加上少量 craft 文档 |

## 3. Hypit 的短板（有证据）

1. **拉片不结构化**
   - 理解主要靠 agent 看帧网格，产物是散文 Markdown，不是机器可读的 IR。
   - 仓库里搜不到 OCR、动效曲线拟合、相机反解、音乐结构分析（`grep ocr|paddle|ssim|lpips` 均无结果）。
   - 文字的精确位置、字体、出入场曲线，全靠 agent 目测。
2. **没有量化验证**
   - "Done means watched"：质量靠 agent 和用户观看判断。
   - 和原片之间没有差分指标、回归测试或分数。
3. **改编优先，不追求还原**
   - 文档明确写道："need not guess the original author's source code or component structure"。
   - 这对创意改编合理，但做不了"高保真复刻"，也无法衡量复刻得像不像。
4. **模型覆盖窄**
   - 视频没有 Kling、Veo、Vidu、Runway、Luma、海螺。
   - 没有专门的数字人（OmniHuman）、口型重配（Sync）、动作迁移、虚拟试穿、3D、音乐生成、视频生音效、超分。
5. **没有能力注册表**
   - 模型边界散落在 craft 散文和各 package 文档里。
   - 选模型靠 agent 判断，没有统一的"能力、边界、价格、许可"数据，也没有探针去测边界。
6. **时间主轴偏向语音**
   - 口播、播客、采访、排行榜是强项。
   - 无语音的音乐卡点、产品 MG、电影感短片、舞蹈和变装，没有一等公民的锚点类型（节拍虽然被提到是"同级时间源"）。
7. **出口单一**：只能走 Hypit 自身的 HTML/Chromium 管线，没有剪映草稿、OTIO、AE 等导出。
8. **许可限制**：不能拿它做 SaaS 或商业再分发。rea-video 应当 **clean-room 实现、不复制代码**，以便采用宽松许可（例如 MIT，与 REA 一致）。

## 4. rea-video 的超越方案

| # | 方向 | 具体做法 | 对应短板 |
|---|---|---|---|
| A | **结构化逆向** | 确定性 CV（切镜、OCR 轨迹、SAM 分割、相机运动、节拍、字级对齐）加上 VLM 语义。产出 Video IR，每个元素附证据（帧号）、置信度和 unknowns | 1 |
| B | **能力地图 + 路由器 + 探针** | `rea_video/capabilities/` 数据化记录每个模型；路由规则把镜头特征映射到原语和模型；每个原语一组探针测试，新模型上线自动跑、自动刷新分数 | 4、5 |
| C | **差分验证** | 每镜 scorecard：切点 F1、OCR 文本一致、布局 IoU、SSIM/LPIPS（代码层）、DreamSim/CLIP（生成层）、身份一致性、轨迹误差、节拍和口型对齐。agent 循环修复最差的镜头 | 2 |
| D | **双模式 + 分层保真度** | fidelity / adapt 两种模式，按层设定保真度（结构、节奏、运镜、文字、动效、音乐、人物） | 3 |
| E | **多锚点时间** | 词、节拍、动作事件、绝对帧都是锚点；音乐卡点和 MG 不依赖语音 | 6 |
| F | **更广的覆盖 + 国内生态** | 可灵、即梦 / Seedance、Vidu、海螺、MiniMax 语音、豆包 VLM、GPT Image 2.5 中文字；加上数字人、试穿、3D、Foley、超分 | 4 |
| G | **开放导出** | 导出 Remotion 工程、OTIO，以及剪映草稿（规划中）；可考虑与 Hypit SVML 互通 | 7 |
| H | **成本工程** | 镜头级成本预估；先确认关键帧图；480p 草稿后超分；同一角色的多个镜头合并成一次生成 | — |

## 5. 如何证明"比 Hypit 好"

建一个公开的 **rea-video-bench**：

- **素材分类**：
  1. 口播 UGC
  2. 播客、采访
  3. 产品 MG 宣传片（大量文字和 UI）
  4. 电商实拍带货
  5. 变装、舞蹈（动作强相关）
  6. 音乐卡点混剪
  7. 屏幕录制演示
- **客观指标**：结构还原（镜头数、切点 F1）、文字准确率、节奏和节拍对齐、主体一致性、口型同步、成本、耗时。
- **主观指标**：盲测 A/B，比较像不像、好不好看、能不能投放。
- **合成子集**：随机生成"已知源码"的 Remotion 视频，测量逆向还原率（自带 ground truth）。

Hypit 的优势领域是类别 1 和 2。rea-video 先在类别 3（产品 MG）建立明显优势，再全面对比。
