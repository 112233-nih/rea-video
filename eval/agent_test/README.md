# agent 盲测：用 rea-video 拆解文章视频（给 Claude Code 编排用）

目的：检验"另一个 agent 只靠本仓库的 Skill 和工具，能不能把视频怎么做的拆对"。上一轮用的是 dsh（DeepSeek V4.1 Flash），这一轮可以测 zcode 或其他 agent。

被测 agent 只拿到 `JOB.md`，看不到答案；Claude Code 负责准备环境、逐条调用、收集结果、按本文评分。

## 0. 准备（Claude Code 执行）

```bash
REPO=~/code/github/rea-video
BLIND=/tmp/rea-video-blind          # 盲测副本：不含 eval/、out/、.cache/（里面有答案）
cd $REPO
pip install -e ".[ocr]"             # 需要 ffmpeg；首次分离人声会从 GitHub 下载约 67 MB 模型

# 0.1 确定性回归（不需要 agent）
export CAPSULE_CINEMA_ROOT=~/code/github/capsule-cinema
rea-video eval eval/labels.yaml   -o out/eval_main     # 期望 duration_weighted_accuracy ≈ 0.983（2026-11-02 从头解码）
rea-video eval eval/holdout.yaml  -o out/eval_hold     # ≈ 0.996
rea-video eval eval/holdout2.yaml -o out/eval_hold2    # ≈ 0.989
rea-video eval eval/articles.yaml -o out/eval_articles # ≈ 0.886（逐条见下表"工具 v0.3"列）
python3 -m pytest -q                                   # 26 passed

# 0.2 盲测副本
rm -rf $BLIND && mkdir -p $BLIND/videos $BLIND/runs
rsync -a --exclude eval --exclude out --exclude .cache --exclude .git $REPO/ $BLIND/
cp $REPO/eval/media/articles/v*.mp4 $BLIND/videos/
```

视频：`eval/media/articles/v01.mp4 … v13.mp4`，编号对照在同目录 `mapping.tsv`（不要给被测 agent）。如果文件丢了，从两篇文章重新下载：
mp.weixin.qq.com/s/s7i_fQmwWo9SbRoV-KDZ9g（a1）、mp.weixin.qq.com/s/aXhPx0eQu9ngYxuyvsFE4Q（a2）。

## 1. 调用被测 agent（每条视频一个新会话）

在 `$BLIND` 目录里启动 agent，把 `JOB.md` 的 `{V}` 换成编号作为任务，例如：

```bash
cd $BLIND
for V in v01 v02 v03 v04 v05 v06 v07 v08 v09 v10 v11 v12 v13; do
  sed "s/{V}/$V/g" $REPO/eval/agent_test/JOB.md > runs/JOB_$V.md
  # 换成 zcode 的非交互调用方式，例如：zcode -p "$(cat runs/JOB_$V.md)" > runs/$V.log 2>&1
done
```

记录每条：耗时、是否成功写出 `runs/vXX/AGENT_REPORT.md`、日志里有没有读 `$BLIND` 以外的文件或上网。
建议 v11、v09、v03、v04 先跑（最能区分好坏），再跑其余。

## 2. 真值与评分

只评有真值的 11 条（v02、v06 真值不确定，只看报告是否把不确定说清楚）。每条主分 0–2：

| 编号 | 文章说明 / 人工看画面 | 2 分（全对） | 1 分（部分对） | 0 分 | 工具 v0.3 | dsh（工具 v0.2） |
|---|---|---|---|---|---|---|
| v01 | a1：OiiOii 复刻成片（视频模型生成） | AI 视频 | — | 其他 | AI ✓ | 2 |
| v02 | a1：原始参考片（铅笔上的定格小人） | 不评分：写明"定格动画还是 AI 待确认"算好 | | | AI | — |
| v03 | a1：直接用视频模型复刻 | 全片 AI（含 AI 生成的铅笔素描过渡、结尾镜头拉远露出桌面） | AI 为主但混进"代码卡/静态图推拉" | 其他 | AI ✓ | 0（判出代码白底卡 + 静态图推拉） |
| v04 | a1：别人的产品推广片，黑底 + 玻璃质感 3D 星星/铬金属 + 文字动效 | 3D 元素 + 代码/MG 文字 | 只说出其一 | 说成录屏/实拍 | 3D 65% + AI 35%（1 分） | 0（"无 3D"，判成界面 + 录屏） |
| v05 | a1：OiiOii「视频模型 + Coding」成片：模型做立体装饰，代码做文字/按钮/转场，界面是真实产品截图 | 三者都说到 | 说到其中两样 | 一样都没对 | 3D 63% + 代码 22% + AI 12%（1 分） | 1 |
| v06 | a1：吊扇视角换装原片 | 不评分：写明"实拍还是 AI 待确认"算好 | | | AI | — |
| v07 | a1：OiiOii 换成男生版 | AI 视频 | — | 其他 | AI ✓ | 2 |
| v08 | a2：「广告原始 / 广告复刻后」左右拼屏，两边都是 UI 动效 | 拼屏 + 代码/UI 动效 | 只说出拼屏或只说出 UI 动效 | 其他 | 代码 80% + 3D 20%（1 分） | 1（说成录屏） |
| v09 | a2：Google Gemini 发布片原片，2D UI 动效 | 代码/UI 动效，无 3D | 代码为主但带 >15% 3D | 其他 | 代码 81% + 3D 19%（1 分） | 2 |
| v10 | a2：OiiOii 第一版「视频生成 + Coding」 | 代码 UI 动效为主 | 代码为主但带 >15% 3D | 其他 | 代码 92%（2 分） | 2 |
| v11 | a2：TikTok 带货原片，手机实拍（真实厨房、真手、男声口播） | 实拍 | "实拍还是 AI 待确认" | AI | 实拍（2 分） | 0（AI 100% 并调高置信度） |
| v12 | a2：OiiOii 榨汁杯复刻（文件带 AIGC 隐式标识） | AI 视频 | — | 其他 | AI ✓ | 2 |
| v13 | a2：变装换人物（QQ 飞车风，游戏 UI 是画进画面的） | AI 视频 | — | 其他 | AI ✓ | 2 |

满分 22。参考线：工具 v0.3 不经 agent 复核 18；dsh 上一轮 14（当时工具是 v0.2，v11 无论如何拿不到分）。

另外记录这几项（不计入主分，但写进结论）：

- **看不了图时有没有乱改**：agent 自己说看不了图、又推翻了工具的制作方式判断，每次记一笔。上一轮 dsh 有 2 次推翻错误。
- **关键细节**：v08 认出左右对照拼屏；v12 引用 AIGC 标识；v13 指出游戏 UI 是画面内容不是叠加层；v05 把界面截图列为真实素材；v11 指出平台转码导致噪声证据失效。
- **声音**：只看有没有把人声 / BGM / 音效说清楚、来源是否标了"待确认"；这批视频没有声音真值，不打分。
- **成本与稳定性**：每条耗时、失败重试次数、token 用量（如果能拿到）。

## 3. 结果写法

在 `$REPO/out/agent_eval/<agent 名>/RESULT.md` 写：

1. 总表：编号 | agent 结论（FINAL 行）| 得分 | 工具 v0.3 | 备注
2. 总分、与工具 v0.3 / dsh 的对比
3. 推翻工具的每一次：对还是错、理由是什么
4. 发现的 Skill 或工具问题（可复现的写出视频编号和镜头时间）

把 `runs/` 下的报告一并拷过去：`cp -r $BLIND/runs $REPO/out/agent_eval/<agent 名>/`。
