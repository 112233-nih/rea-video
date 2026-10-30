# 能力地图（Capability Map）

> 模型就是 rea-video 的"指令集"。逆向一条视频，就是把每个镜头反推成"在当前模型能力下最可控的一组生成调用或代码渲染"。
> 这里是那本指令集手册：数据化、可校验、带验证日期，并且会过期。

人类可读的总表见 [MATRIX.md](MATRIX.md)，由脚本自动生成。

## 三层结构

```
signals（镜头特征，拉片时检测）
   │  routing.yaml
   ▼
primitives（与厂商无关的能力原语）  ◄── primitives.yaml
   │  models/*.yaml 中的 primitives 字段
   ▼
models（具体模型/工具，带边界、价格、许可、来源）
```

- **primitives.yaml**：共 43 个原语，覆盖分析、图像、视频、人物驱动、3D、音频、代码渲染、后期，另有 16 个验证指标。路由只认原语，换厂商时规则不用改。
- **models/*.yaml**：每个模型一个条目，按类别分文件：
  - `video`：视频生成与编辑
  - `character`：动作迁移、数字人、口型重配、试穿
  - `image`：图像
  - `audio`：音频
  - `threed`：3D 生成
  - `analysis`：拉片用的 VLM 和确定性工具
  - `render-post`：代码渲染、合成、超分、补帧
- **routing.yaml**：包含以下几部分
  - 信号定义
  - 两级拆分规则（剪辑镜头 → 生成单元 → 分层）
  - 画面路由 R01–R16、音频路由 A01–A05、后期路由
  - 成本策略、确认节点、提示词纪律

## 模型条目字段

| 字段 | 说明 |
|---|---|
| `id` | 全局唯一，小写加连字符 |
| `status` | `ga` / `preview` / `limited` / `deprecated` / `shutdown`；路由器跳过 shutdown |
| `access` | `app` / `api` / `api-aggregator` / `open-weights` |
| `primitives` | 支持的原语 |
| `limits` | 时长、分辨率、参考数量、关键帧数等边界 |
| `cost.usd_per_s` | 相对成本，用于路由排序和预算估算 |
| `license` | `commercial: true / false / conditional`，附说明；`regions_excluded` 列出地区限制 |
| `verified.basis` | 可信度从高到低：`probe`（我们自测）> `user`（用户实测）> `vendor`（官方）> `third-party`（第三方）> `knowledge`（常识或训练知识） |
| `confidence` | `high` / `medium` / `low` |
| `probe_scores` | 探针测试得分，预留，由探针自动写入 |

## 维护流程

1. **新增或更新模型**：编辑对应的 `models/*.yaml`，写明来源链接和 `verified.date`。
2. **校验**：运行 `python3 scripts/validate_capabilities.py --matrix`。脚本检查 schema、引用完整性、"首选模型是否真的支持该原语"、原语空缺和过期条目（超过 90 天）。
3. **用户实测结论**（例如"GPT Image 2.5 的中文字最好"）：写进 `strengths`，并把 `basis` 标为 `user`。
4. **模型下线**：把 `status` 改成 `shutdown`，条目保留，同时从路由规则里移除。
5. **探针（P1）**：每个原语一组小测试（`primitives.yaml` 的 `probe` 字段），新模型上线后自动跑，分数写回 `probe_scores`，路由排序会优先参考它。

## 已知待补

- 很多条目的发布月份和细节规格来自第三方汇总，标为 `medium` 或 `low`，需要用探针或官方文档复核。
- Kling 4.0 正式版预计 2026-10 上线，需要复核规格。
- 尚未收录：海外 UGC 数字人平台（HeyGen 等）、Pika、Higgsfield 套件、更多国内图像模型（即梦图片、可图等）、剪映 / CapCut 导出能力。
