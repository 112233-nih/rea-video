你在 rea-video 仓库的一份副本里（当前目录）。先读 AGENTS.md、skills/rea-video/SKILL.md，以及它引用的 skills/rea-video/references/（taxonomy、audio、materials、output-template）。严格按 SKILL.md 的流程工作。

任务：拆解视频 videos/{V}.mp4。它是从微信公众号文章下载的转码版（30fps、二次压缩），你不知道它是怎么做的，需要你判断。

1. 环境没装好就先按 SKILL.md 第 0 节安装（需要 ffmpeg；可以直接用 `python3 -m rea_video.cli` 代替 `rea-video`）。
2. 运行 decompose，输出到 runs/{V}/。VLM 配置可用时（例如 `--env-file ~/code/github/capsule-cinema/.env`）按 SKILL.md 建议加 `--vlm`；不可用就不开，并在报告里写明。
3. 按 SKILL.md 自己复核。先确认你能不能看图：能看就看 storyboard.jpg、shots/、低置信镜头；看不了就遵守 SKILL.md「看不了图时的规则」。
4. 按 references/output-template.md 的结构写 runs/{V}/AGENT_REPORT.md（中文），必须包含：
   - 一句话结论：制作方式（实拍 / AI 视频 / 数字人 / 3D / 代码渲染 / 录屏截图 / 静态图推拉 / 静帧）及各占比
   - 分镜表、真实素材、声音（人声 / BGM / 音效及来源）、不确定点
   - 最后一行固定格式：`FINAL: <主要制作方式> | <置信度 0-1> | <能否看图: yes/no> | <VLM 是否生效: yes/partial/no>`（以 `rea-video summary` 的 `VLM:` 一行为准，请求了但调用失败算 no）
5. 不要修改代码，不要上网搜索这条视频，不要读当前目录以外的文件。
