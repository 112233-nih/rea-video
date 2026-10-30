# capsule-cinema 对接

这两个脚本用 **capsule-cinema 自己的 Python 环境**运行（它们会 import capsule-cinema 的 `lib/` 和 `scripts/`，并读取它的 `.env`）。rea-video 本身只依赖 numpy/opencv/pillow/pyyaml。

```bash
CC=~/code/github/capsule-cinema          # capsule-cinema 仓库
PY=$CC/.venv/bin/python                  # 或 python3.12

# 1) 拆解（建议开 VLM，复用 capsule-cinema 的 Gemini 配置）
rea-video --env-file $CC/.env decompose ref.mp4 -o out/ref --vlm

# 2a) 生成胶囊草稿（不改 capsule-cinema 代码；通过 tool_factory 注入 rea-video 的拆解结果）
$PY integrations/capsule_cinema/to_capsule.py --breakdown out/ref/breakdown.json \
    --capsule-root $CC --capsule-name my_ref_remake            # 加 --write-capsule 才落盘到 capsules/

# 2b) 复刻 AI 类镜头：生成任务 → 调 capsule-cinema 生成 → 按原时间线拼回并出对比片
rea-video remake-plan out/ref/breakdown.json --capsule-root $CC --engine seedance2.0
$PY integrations/capsule_cinema/run_remake.py --jobs out/ref/remake/jobs.json --dry-run   # 先看任务
$PY integrations/capsule_cinema/run_remake.py --jobs out/ref/remake/jobs.json             # 真正调用（会产生费用）
rea-video assemble out/ref/breakdown.json out/ref/remake/results.json
```

- AI 视频 / 3D / 实拍 / 数字人镜头：默认用原镜头的**首尾帧 + VLM 提示词**做 `first_last_frame` 生成（`--mode image_to_video` 只用首帧）。
- 静态图推拉、静帧：不调模型，`assemble` 用测得的缩放/平移参数在本地重建。
- 代码渲染、录屏镜头：默认保留原片（`keep_original`），重建请按报告中的文字轨/版式用 Remotion 实现。
- 只复刻部分镜头：`run_remake.py --only 2,5`。
