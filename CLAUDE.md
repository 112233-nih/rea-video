# rea-video：给 Agent 的说明

- 用户要拆解视频、判断制作方式、标真实素材、拆音乐音效、复刻时，**先读 `skills/rea-video/SKILL.md`**，按里面的流程做，并复核低置信镜头。
- 开发约定：
  - 拆解逻辑在 `rea_video/`
  - 规则改动后跑 `python3 -m pytest -q` 和 `rea-video eval eval/labels.yaml` / `eval/holdout*.yaml`，结果更新到 `eval/RESULTS.md`
  - 模型能力地图在 `capabilities/`，改完跑 `python3 scripts/validate_capabilities.py --matrix`
- 不要提交任何密钥；VLM 配置通过 `--env-file` 读取。
