# 对接 capsule-cinema 复刻

capsule-cinema 的脚本必须用它自己的 Python 环境运行（`$CC/.venv/bin/python` 或 python3.12）。

```bash
CC=~/code/github/capsule-cinema; PY=$CC/.venv/bin/python
rea-video remake-plan out/ref/breakdown.json --capsule-root $CC --engine seedance2.0   # 也可以 --mode image_to_video / --labels ai_video
$PY integrations/capsule_cinema/run_remake.py --jobs out/ref/remake/jobs.json --dry-run  # 先给用户看任务和费用
$PY integrations/capsule_cinema/run_remake.py --jobs out/ref/remake/jobs.json            # 用户确认后再跑
rea-video assemble out/ref/breakdown.json out/ref/remake/results.json                   # 拼回原时间线 + compare.mp4
$PY integrations/capsule_cinema/to_capsule.py --breakdown out/ref/breakdown.json --capsule-root $CC --capsule-name xxx [--write-capsule]
```

- jobs.json 里每个任务的 `executor` 取值：
  - `capsule_cinema`：调模型生成
  - `local_ffmpeg`：静态图本地重建
  - `keep_original`：保留原片，比如代码渲染、录屏、真实素材
- `user_assets`：这个镜头需要用户提供的真实素材。生成结果只是近似，要提醒用户。
- `to_capsule.py`：通过 capsule-cinema 自带的 `tool_factory` 注入拆解结果，不改 capsule-cinema 代码，生成的配方草稿默认是 `review_required`。
