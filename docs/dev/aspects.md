# 拍法分析维度（aspects）：怎么扩展

每个维度是 `rea_video/aspects/` 下的一个模块，负责一个问题的全流程，加新维度不用改流水线、提示词、复核模板、报告或评测：

| 步骤 | 方法 | 产出 |
|---|---|---|
| 逐镜测量 | `measure(ctx)`：`ctx.frames` 采样帧、`ctx.subjects` 每帧主体（人脸 / 清晰区域 / 画面中心）、`ctx.features` 运动和纹理特征 | `shot.aspects.<key>.measured` |
| 看图提问 | `shot_fields` / `video_fields`：`Field(key, 中文名, 问题, prefer)` | 自动进 VLM 提示词和 `review_answers.json` |
| 逐镜解读 | `interpret(shot)` | `shot.aspects.<key>.result` |
| 整片汇总 | `summarize(shots, b)`：返回 `result`（可评测的值）、`lines`（摘要行）、`fields`（测量 / VLM / 看图 / 结论对照表） | `breakdown.aspects.<key>` |
| 评测 | `check(gt, summary)`，默认比较 `result` 里的字段 | `eval/*.yaml` 里写 `aspects: {<key>: {<字段>: 可接受值}}` |

`prefer="seen"` 的字段以看图结论为准（agent 优先于 VLM），`prefer="measured"` 以测量为准、看图结论并列显示。

现有维度：`framing` 取景（景别、机位角度、构图）、`camera` 机位（手持/固定、运镜、镜头、设备）、`lighting` 打光（主光方向、光比、软硬、色温、布光方案 + 器材，规则表 `SETUPS` 可加行）、`editing` 剪辑（节奏、机位数、跳切、推近切、剪气口、插入占比）、`format` 形式（口播 / 桌拍 / 手持拍物品 / vlog）。

新增维度：写一个 `Aspect` 子类，模块末尾 `register(...)`，在 `aspects/__init__.py` 里按依赖顺序 import。要用别的维度结果的（如 `format` 用 `editing`），放在它们后面。
