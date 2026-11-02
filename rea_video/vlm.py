"""OpenAI-compatible multimodal client (Gemini via proxy, Doubao, GPT, Qwen-VL...).

Config (first non-empty wins):
  REA_VLM_BASE_URL / REA_VLM_API_KEY / REA_VLM_MODEL
  VIDEO_ANALYSIS_BASE_URL / VIDEO_ANALYSIS_API_KEY / VIDEO_ANALYSIS_MODEL_NAME   (capsule-cinema)
  GEMINI3_BASE_URL / GEMINI3_API_KEY / GEMINI3_MODEL_NAME                        (capsule-cinema)
REA_VLM_MODEL may be a comma-separated fallback list.
REA_VLM_MEDIA = both | video | frames  (default both: a short clip as data:video/mp4 plus keyframes)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .classify import LABELS
from .util import LOG


@dataclass
class VLMConfig:
    base_url: str
    api_key: str
    models: list[str]
    timeout: float = 180.0
    media: str = "both"

    @classmethod
    def from_env(cls) -> "VLMConfig | None":
        """First complete config wins; later configs on the same endpoint and key add their
        models as fallbacks (capsule-cinema keeps one model in VIDEO_ANALYSIS_* and a list in GEMINI3_*)."""
        cfg: VLMConfig | None = None
        for prefix, mkey in (("REA_VLM", "REA_VLM_MODEL"), ("VIDEO_ANALYSIS", "VIDEO_ANALYSIS_MODEL_NAME"), ("GEMINI3", "GEMINI3_MODEL_NAME")):
            base, key = os.getenv(f"{prefix}_BASE_URL", "").rstrip("/"), os.getenv(f"{prefix}_API_KEY", "")
            models = [m.strip() for m in os.getenv(mkey, "").split(",") if m.strip()]
            if not (base and key and models):
                continue
            if cfg is None:
                cfg = cls(base_url=base, api_key=key, models=models,
                          timeout=float(os.getenv("REA_VLM_TIMEOUT", os.getenv("VIDEO_ANALYSIS_TIMEOUT_SECONDS", "180"))),
                          media=os.getenv("REA_VLM_MEDIA", "both"))
            elif prefix != "REA_VLM" and base == cfg.base_url and key == cfg.api_key:
                cfg.models += [m for m in models if m not in cfg.models]
        return cfg

    def describe(self) -> str:
        host = re.sub(r"^https?://", "", self.base_url).split("/")[0]
        return f"{','.join(self.models)} @ {host}"


SETUP_HINT = ("建议配置 VLM（任意 OpenAI 兼容的多模态模型，如 Gemini / GPT-4o / 豆包 / Qwen-VL）："
              "设置 REA_VLM_BASE_URL、REA_VLM_API_KEY、REA_VLM_MODEL（可写多个模型逗号分隔做备选），再加 --vlm 重跑")


def vlm_note(info: dict, review: dict | None = None) -> str:
    """One line for reports / summaries: did the VLM (or an agent's review) actually contribute, and what to do if not."""
    base = _vlm_note(info)
    if review and review.get("shots"):
        seen = f"由 {review.get('by', 'agent')} 看图复核 {review['shots']}/{review.get('of', '?')} 个镜头，已并入结论"
        return seen if (info.get("status") or "off") == "off" else f"{base.split('；')[0]}；{seen}"
    return base


def _vlm_note(info: dict) -> str:
    status = info.get("status") or ("ok" if info.get("used") else "off")
    if status == "ok":
        return f"VLM 已生效（{info.get('model', '')}）"
    if status == "partial":
        return (f"VLM 部分生效（{info.get('model', '')}：{info.get('ok_shots', 0)}/{info.get('asked_shots', 0)} 个镜头有结果，"
                f"整片分析{'有' if info.get('whole_ok') else '失败'}）；失败的镜头只靠确定性取证")
    limited = "实拍/AI/3D 的区分只靠像素统计，可信度有限"
    if status == "failed":
        err = (info.get("errors") or [""])[0]
        return f"VLM 已请求但全部调用失败（{info.get('model', '')}：{err[:80]}），本次等同未开 VLM；{limited}。可稍后重试或换模型"
    if status == "not_configured":
        return f"VLM 已请求但没找到配置，本次未开 VLM；{limited}。{SETUP_HINT}"
    return f"未开 VLM；{limited}。能看图的 agent：运行 rea-video review 逐镜看图并 apply-review 并入；否则{SETUP_HINT}"


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("no JSON object in VLM response")
    return json.loads(m.group(0))


class VLMClient:
    # after this many requests in a row fail on every model, stop calling the service for this run
    MAX_DEAD_REQUESTS = 2

    def __init__(self, cfg: VLMConfig, cache_dir: Path | None = None):
        self.cfg = cfg
        self.cache_dir = cache_dir
        self.dead_requests = 0
        self.models = list(cfg.models)  # reordered: the last model that answered goes first
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def gave_up(self) -> bool:
        return self.dead_requests >= self.MAX_DEAD_REQUESTS

    def _post(self, model: str, content: list[dict], max_tokens: int) -> str:
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                           "max_tokens": max_tokens, "temperature": 0.2}).encode()
        req = urllib.request.Request(self.cfg.base_url + "/chat/completions", data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.api_key}"})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            data = json.loads(resp.read().decode())
        return data["choices"][0]["message"]["content"]

    def ask_json(self, prompt: str, media: list[tuple[Path, str]], max_tokens: int = 4096) -> dict:
        h = hashlib.sha1(prompt.encode())
        for p, mime in media:
            h.update(mime.encode())
            h.update(hashlib.sha1(p.read_bytes()).digest())
        key = h.hexdigest()
        if self.cache_dir and (self.cache_dir / f"{key}.json").is_file():
            return json.loads((self.cache_dir / f"{key}.json").read_text(encoding="utf-8"))
        if self.gave_up:
            raise RuntimeError("VLM skipped: service failed on every model for earlier requests")
        content = [{"type": "image_url", "image_url": {"url": _data_uri(p, mime)}} for p, mime in media]
        content.append({"type": "text", "text": prompt})
        last: Exception | None = None
        for model in list(self.models):
            for attempt in range(2):
                try:
                    t = time.time()
                    text = self._post(model, content, max_tokens)
                    out = parse_json(text)
                    out["_model"] = model
                    out["_seconds"] = round(time.time() - t, 1)
                    if self.cache_dir:
                        (self.cache_dir / f"{key}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.dead_requests = 0
                    if self.models[0] != model:
                        self.models.remove(model)
                        self.models.insert(0, model)
                    return out
                except (urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    last = exc
                    LOG.warning("VLM %s attempt %d failed: %s", model, attempt + 1, str(exc)[:200])
                    time.sleep(1.5)
        if not isinstance(last, (ValueError, KeyError)):  # transport/server errors, not a bad answer
            self.dead_requests += 1
        raise RuntimeError(f"all VLM models failed: {last}")


SHOT_PROMPT = """你是视频制作逆向工程专家。判断这个镜头的「底层画面」是用什么方式制作的，并给出可复刻的描述。
只依据你在画面里看到的证据；不确定时如实给出概率分布。

底层画面类别（label_probs 的 key，概率和为 1）：
- live_action：真实摄像机/手机实拍（真实人物与环境、真实光影与物理、传感器噪声、手持抖动、对焦呼吸、运动模糊自然）
- ai_video：AI 生成视频（文生/图生/参考生视频）。常见破绽：物体或手指形变、纹理"沸腾"闪烁、物理不合理、文字乱码、背景人物畸形、过于完美的光线与皮肤、动作缓慢漂浮、镜头结尾定格
- avatar：数字人口播（一张人像被音频驱动说话，嘴型驱动、头部轻微摆动、身体几乎不动）
- cg_3d：传统 3D 渲染（Blender/C4D/UE），几何干净、材质规则、光影一致、运动精确无形变
- code_render：代码/模板渲染的 2D 画面（MG 动效、文字排版、UI、图表、PPT 式卡片）
- screen_capture：屏幕录制或网页/应用截图（真实界面元素、光标、滚动）
- still_motion：一张静态图片做推拉摇移（Ken Burns），画面内部无独立运动
- static_hold：静帧定格（图片或卡片停留不动）
- anim_2d：2D 动画（手绘逐帧、Live2D、AE/Spine 角色动画、扁平插画风动画）：线条和平涂色块、角色有骨骼或逐帧动作，常见"一拍二"（每两帧才变一次）
- stop_motion：定格动画（黏土、毛毡、纸偶、乐高等实物逐帧拍摄）：真实材质和灯光、动作一顿一顿、帧间轻微闪烁、指纹和纹理细节

如果画面是实拍或写实画面，再拆「怎么拍、怎么打光」（aspects 里各项），纯图形/文字/界面画面这些字段写"不适用"。

另外判断画面里有没有「引入的真实素材」：实拍片段、真实照片、商品实拍图、品牌 Logo、网页/App 截图或录屏。
请在 embedded_assets 里逐个列出。注意两种情况不同：画面本身由 AI 生成 / 3D 建模 / 实拍时，里面出现的商品、Logo、人物
只是复刻要准备的参考（照样列出，系统会归为「参考素材」）；只有贴进版式里的录屏、截图、照片、视频片段才是真实像素。

注意区分：卡通/3D 风格的 AI 视频（形变、闪烁、不精确）与真正的 3D 渲染（几何稳定、精确）；
AI 生成的写实视频与实拍（看手、文字、背景细节、物理）；AI 生成的动画风视频（形变、线条游移）与真正的 2D 动画（线条稳定、按帧精确）；
AI 仿定格风格（材质在帧间漂移）与真正的定格动画（同一实物、光照不变、只有逐帧位移）。

实拍（或定格）画面如果被 AI 二次加工过，在 ai_edits 里逐个列出：换脸、对口型/改台词、整段风格化重绘、AI 换装、抠像换背景/绿幕、
加删换物体、美颜瘦脸。依据要写看得到的破绽（脸部边缘、嘴型和下巴不跟、头发与背景交界、光线不一致）。没有就写 []。

确定性分析给出的参考信号（可能有误，以画面为准）：
{hints}

只返回 JSON，不要任何其他文字：
{{
  "label_probs": {{"live_action": 0, "ai_video": 0, "avatar": 0, "cg_3d": 0, "code_render": 0, "screen_capture": 0, "still_motion": 0, "static_hold": 0, "anim_2d": 0, "stop_motion": 0}},
  "image_source": "ai_image|photo|graphic|screenshot|unknown",
  "evidence": ["具体可见证据，尽量指明位置/时间"],
  "generator_guess": "可能的生成模型或工具（如 Seedance/Kling/Veo/Hailuo/Blender/Remotion/剪映 模板），无法判断写 unknown",
  "description": "镜头内容：主体、动作、场景、景别、运镜、光线",
  "shot_size": "特写|近景|中景|全景|远景",
  "camera_move": "固定|推|拉|摇|移|跟|环绕|手持",
  "style": "画风/质感关键词",
  "overlay_text": ["后期叠加在画面上的文字（字幕/标题），不含场景里的实物文字"],
  "aspects": <<SHOT_ASPECTS>>,
  "material_origin": "real|ai_generated|rendered|mixed（画面主要来自真实素材 / AI 生成 / 制作方渲染 / 混合）",
  "embedded_assets": [{{"kind": "real_footage|photo|screenshot|screen_recording|product_photo|logo|ai_image", "description": "是什么、在画面哪里", "is_real_material": true}}],
  "ai_edits": [{{"kind": "face_swap|lip_sync|restyle|outfit_swap|bg_replace|object_edit|beautify", "evidence": "看到的破绽"}}],
  "replication": {{
    "image_prompt": "复刻首帧的中文图像提示词（主体、构图、光线、风格，具体可执行）",
    "video_prompt": "复刻该镜头的中文视频提示词：写清动作过程和结束状态、运镜；只写想要的内容，不写否定句",
    "video_prompt_en": "same video prompt in English"
  }}
}}"""


WHOLE_PROMPT = """你是短视频拆解专家。完整观看并**仔细收听**这条视频，拆解它的结构、文案、声音与整体制作方式。
声音要拆到可以复刻：旁白是真人还是 TTS、音色；背景音乐的风格/情绪/节奏/是否 AI 生成或知名歌曲；
判断音乐来源时结合视频形式：AI 音乐 MV（整片一首歌、歌词唱的就是画面内容）大概率是 AI 生成的歌；普通视频的垫底配乐大概率是现成曲库；
能认出歌名就写出来；lyrics_match_content 表示歌词是否在唱这条视频的具体内容/角色/商品。
每一个音效出现的时间点、类型，以及它对应画面上的什么事件。
剪辑包装要拆全：字幕之外的花字、标题、章节牌、标签（含跟着物体走的标注）逐个列出时间、样式和动画；
后期贴上去的表情包、GIF 动图、emoji、贴纸、画中画小窗、箭头圈注逐个列出出现/消失时间和位置（画面本身的内容不算）。
已检测到的镜头切分（秒）：{shots}

只返回 JSON：
{{
  "summary": "一句话概括",
  "format": "口播|剧情|商品展示|知识讲解|MV|混剪|其他",
  "hook": "开头 3 秒如何抓人",
  "segments": [{{"start": 0.0, "end": 3.0, "beat": "这一段在叙事上的作用", "copy": "这一段的旁白/字幕文案", "audio": "人声/音乐/音效"}}],
  "narration": {{"type": "tts|human|none|mixed", "voice": "性别/年龄感/音色", "style": "语速、情绪、口癖", "language": "zh|en|...",
                 "lines": [{{"start": 0.0, "end": 2.0, "text": "台词/旁白原文"}}]}},
  "music": {{"present": true, "segments": [{{"start": 0.0, "end": 10.0}}], "style": "风格", "genre": "流派", "mood": "情绪",
            "instruments": ["主要乐器"], "bpm_estimate": 0, "has_vocals": false,
            "source_guess": "ai_generated|stock_library|popular_song|original|unknown", "song_guess": "若是知名歌曲写歌名，否则空",
            "lyrics_excerpt": "若有人声演唱，摘几句歌词", "lyrics_match_content": false,
            "ending": "natural_end|fade_out|hard_cut",
            "beat_synced_cuts": false}},
  "sfx": [{{"t": 0.0, "type": "whoosh|pop|ding|click|typing|impact|laugh|applause|notification|cash|other", "description": "听到的声音",
           "sync_with": "cut|text_pop|action|transition|none"}}],
  "ambience": "环境声（街道、室内、无）",
  "mix": "人声/BGM/音效的音量关系，是否有人声出现时压低音乐（ducking）",
  "subtitles": {{"present": true, "style": "字体/描边/颜色/位置/逐字高亮等"}},
  "text_effects": [{{"t": 0.0, "text": "花字/标题/标签上的字", "kind": "花字|标题|章节牌|标签|弹幕", "style": "字体、颜色、描边、底框、大小",
                    "animation": "弹出/缩放/抖动/打字机/逐字高亮/滑入/无"}}],
  "stickers": [{{"t0": 0.0, "t1": 1.0, "what": "表情包/GIF 动图/emoji/静态贴纸/画中画小窗/箭头圈注", "where": "画面位置",
                "description": "贴的是什么", "animated": true}}],
  "aspects": <<VIDEO_ASPECTS>>,
  "overall_production": "整体制作方式判断（实拍/AI生成/代码渲染/混合）与理由",
  "warnings": []
}}"""


from .aspects import prompt_block as _aspect_block  # noqa: E402

SHOT_PROMPT = SHOT_PROMPT.replace("<<SHOT_ASPECTS>>", _aspect_block(video=False))
WHOLE_PROMPT = WHOLE_PROMPT.replace("<<VIDEO_ASPECTS>>", _aspect_block(video=True))


def shot_hints(features: dict, facts: dict, det_top: str, det_prob: float) -> str:
    keys = ["noise_sigma", "temporal_noise", "flat_ratio", "palette95", "dup_ratio", "camera", "kenburns_score", "local_motion_ratio"]
    parts = [f"{k}={features.get(k):.3g}" if isinstance(features.get(k), float) else f"{k}={features.get(k)}" for k in keys]
    parts.append(f"temporal_family={facts.get('temporal_family')}, pulldown={facts.get('pulldown')}, graphic_score={facts.get('graphic_score')}")
    parts.append(f"确定性初判={det_top} ({det_prob:.0%})")
    return "; ".join(parts)


def normalize_shot_result(raw: dict) -> dict:
    probs = raw.get("label_probs") or {}
    clean = {}
    for k in LABELS:
        try:
            clean[k] = max(0.0, float(probs.get(k, 0.0)))
        except (TypeError, ValueError):
            clean[k] = 0.0
    total = sum(clean.values()) or 1.0
    raw["label_probs"] = {k: v / total for k, v in clean.items()}
    return raw
