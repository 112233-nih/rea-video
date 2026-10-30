"""Where did each sound come from?  AI-generated vs existing material.

music : ai_generated (Suno/Udio/MiniMax Music...) | existing_library (stock/editor library BGM)
        | popular_song (a known commercial track) | unknown
sfx   : library_editor (added in the edit from a sound library) | ai_native (generated together
        with an AI video clip, e.g. Seedance/Veo/Kling native audio) | live_sync (camera sound)
voice : tts | ai_video_native (generated with the clip, lip-synced) | human_recording

The audio alone rarely proves AI vs existing, so the decision combines a *format prior*
(an AI music-MV almost always carries an AI song; an ordinary video's BGM is usually a
library track), content evidence (lyrics written about this very video => custom song),
how the music was fitted (hard cut at the end => trimmed library track), platform music
metadata when supplied, per-clip audio resets at cuts (native audio of generated clips), and
the VLM's listening judgement. Every verdict carries probabilities, evidence and the cheapest
way to confirm it."""
from __future__ import annotations

import numpy as np

from .audio import SR, N_FFT, T_HOP, _stft
from .media import extract_audio

MUSIC_ZH = {"ai_generated": "AI 生成音乐", "existing_library": "现成曲库配乐", "popular_song": "现成知名歌曲", "unknown": "待确认"}
SFX_ZH = {"library_editor": "音效库/剪辑软件后期添加", "ai_native": "AI 视频模型随画面生成（原生音频）", "live_sync": "实拍同期声", "unknown": "待确认"}
VOICE_ZH = {"tts": "TTS 配音", "ai_video_native": "AI 视频原生对白（随画面生成）", "human_recording": "真人录音", "unknown": "待确认"}


def _norm(d: dict) -> dict:
    s = sum(max(0.0, v) for v in d.values()) or 1.0
    return {k: round(max(0.0, v) / s, 3) for k, v in d.items()}


def per_clip_audio(path: str, cuts: list[float], duration: float) -> dict:
    """Does the background sound (noise floor spectrum) jump at picture cuts?  Clips that carry
    their own audio (AI video native sound, camera sound) reset at cuts; a voice-over + BGM
    laid over the whole edit does not."""
    if len(cuts) < 2:
        return {"ratio": None}
    x = extract_audio(path, SR)
    if x is None:
        return {"ratio": None}
    S = _stft(x)
    f = np.fft.rfftfreq(N_FFT, 1 / SR)
    edges = np.geomspace(60, 10000, 25)
    B = np.stack([10 * np.log10((S[:, (f >= a) & (f < b)] ** 2).sum(1) + 1e-12) for a, b in zip(edges[:-1], edges[1:])], 1)
    w = int(0.6 / T_HOP)

    def floor(a, b):
        a, b = max(0, a), min(len(B), b)
        return np.percentile(B[a:b], 15, axis=0) if b - a > 10 else None

    def jump(t):
        c = int(t / T_HOP)
        p, q = floor(c - w, c - 2), floor(c + 2, c + w)
        return None if p is None or q is None else float(np.mean(np.abs(p - q)))

    at = [v for v in (jump(t) for t in cuts) if v is not None]
    bounds = [0.0] + list(cuts) + [duration]
    mids = [(a + b) / 2 for a, b in zip(bounds[:-1], bounds[1:]) if b - a > 1.4]
    within = [v for v in (jump(t) for t in mids) if v is not None]
    if not at or not within:
        return {"ratio": None}
    r = float(np.median(at) / (np.median(within) + 1e-6))
    return {"ratio": round(r, 2), "at_cuts_db": round(float(np.median(at)), 1), "within_shots_db": round(float(np.median(within)), 1),
            "resets_at_cuts": r > 1.6}


def music_ending(audio: dict, duration: float) -> str:
    m = audio.get("music") or {}
    ec = audio.get("energy_curve") or []
    if not m.get("present") or len(ec) < 8:
        return "unknown"
    end = m.get("end") or 0
    body = np.median(ec[: max(1, len(ec) - 6)])
    tail = ec[-2:]
    if duration - end < 1.0 and min(tail) > body - 6:
        return "hard_cut"         # music still at full level when the video stops
    if duration - end < 3.0 and min(ec[-4:]) < body - 15:
        return "ends_with_video"  # faded / resolved right at the end of the video
    return "ends_early"


def classify_sound_sources(b: dict, meta: dict | None = None) -> dict:
    audio = b.get("audio") or {}
    if not audio.get("has_audio"):
        return {}
    whole = b.get("whole_video_vlm") or {}
    wm = whole.get("music") or {}
    nar = whole.get("narration") or {}
    meta = meta or b.get("platform_meta") or {}
    shots = b["shots"]
    dur = b["source"]["duration"]
    labels = [s["base"]["label"] for s in shots]
    ai_share = sum(s["duration"] for s in shots if s["base"]["label"] in ("ai_video", "avatar")) / max(dur, 1e-6)
    live_share = sum(s["duration"] for s in shots if s["base"]["label"] == "live_action") / max(dur, 1e-6)
    music = audio.get("music") or {}
    voice = audio.get("voice") or {}
    clip = b.get("per_clip_audio") or {}
    out: dict = {}

    # ---------------- format context --------------------------------------------------
    fmt = str(whole.get("format", ""))
    has_vocals = wm.get("has_vocals")
    vlm_mv = fmt.upper() == "MV" or "MV" in fmt.upper()
    # without the VLM: wall-to-wall music that also carries a voice, and no separate narration
    # sung vocals sit inside the mix, so HPSS finds only part of them (ratio 0.15+ is enough)
    # the voice must actually be sung/rapped (held pitches or beat-locked syllables), not TTS narration over BGM
    style = voice.get("style")
    sung = bool(voice.get("sung")) or style in ("singing", "rap_or_rhythmic")
    det_mv = bool(sung and music.get("ratio", 0) > 0.85 and voice.get("ratio", 0) > 0.15 and nar.get("type") in (None, "", "none"))
    is_mv = bool(vlm_mv or (has_vocals and music.get("ratio", 0) > 0.8 and nar.get("type") in (None, "", "none")))
    ctx = {"format": fmt or None, "music_mv": is_mv, "vocal_style": style, "music_mv_maybe": (not is_mv) and det_mv and not whole,
           "ai_picture_share": round(ai_share, 3), "live_picture_share": round(live_share, 3)}
    out["format_context"] = ctx

    # ---------------- music -------------------------------------------------------------
    if music.get("present") or wm.get("present"):
        ev: list[str] = []
        p = {"ai_generated": 0.2, "existing_library": 0.6, "popular_song": 0.2}
        if is_mv:
            p = {"ai_generated": 0.6, "existing_library": 0.1, "popular_song": 0.3}
            ev.append("视频形式是音乐 MV（整片是一首带人声的歌）：这类内容的歌大概率是 AI 生成的（先验）")
        elif ctx["music_mv_maybe"]:
            p = {"ai_generated": 0.45, "existing_library": 0.25, "popular_song": 0.3}
            ev.append(f"整片音乐不间断，人声是唱的（持续音高占比 {voice.get('sustained_note_ratio', 0):.2f}）：像一首带人声的歌/音乐 MV，AI 歌概率高（未开 VLM，按 MV 先验但降权）")
        else:
            ev.append("音乐是给画面垫底的配乐：普通视频的配乐大概率来自现成曲库（先验）")
            if voice.get("present") and style == "speech":
                ev.append(f"人声是说话/旁白而不是唱歌（持续音高占比 {voice.get('sustained_note_ratio', 0):.2f}）：属于「旁白 + BGM」的普通视频形式")
        if wm.get("lyrics_match_content") is True:
            p["ai_generated"] += 1.2
            ev.append("歌词唱的正是这条视频的具体内容/角色/商品：为本片定制的歌，几乎只能是 AI 生成或专门创作")
        if wm.get("song_guess"):
            p["popular_song"] += 1.2
            ev.append(f"VLM 认出了歌曲：{wm['song_guess']}")
        sg = wm.get("source_guess")
        if sg in ("ai_generated", "popular_song", "stock_library"):
            key = {"stock_library": "existing_library"}.get(sg, sg)
            p[key] += 0.5
            ev.append(f"VLM 听感判断：{ {'ai_generated': 'AI 生成', 'popular_song': '知名歌曲', 'stock_library': '曲库配乐'}[sg] }")
        mt = (meta.get("music") or {}) if isinstance(meta.get("music"), dict) else {}
        if mt.get("title"):
            original = bool(mt.get("is_original")) or any(k in str(mt.get("title")) for k in ("原声", "original sound"))
            if original:
                p["ai_generated"] += 0.3 if (is_mv or ctx["music_mv_maybe"]) else 0.0
                ev.append(f"平台音乐信息是作者原声「{mt.get('title')}」：不是平台曲库里的现成歌曲")
                p["popular_song"] *= 0.4
            else:
                p["popular_song" if mt.get("popular") else "existing_library"] += 1.5
                ev.append(f"平台音乐信息：「{mt.get('title')}」{('— ' + str(mt.get('author'))) if mt.get('author') else ''}（现成曲目）")
        ending = music_ending(audio, dur)
        if ending == "hard_cut":
            if is_mv or ctx["music_mv_maybe"]:  # a sung song cut mid-phrase: a clip of a released song, or just a trimmed AI song
                p["existing_library"] += 0.1
                p["popular_song"] += 0.1
            else:
                p["existing_library"] += 0.35
                p["popular_song"] += 0.15
            ev.append("片尾音乐被硬切断（还在满音量时视频就结束了）：常见于截取现成曲目")
        elif ending == "ends_with_video":
            p["ai_generated"] += 0.15
            ev.append("音乐与视频同时收尾：按片长剪辑或定制过（两种来源都可能，弱证据）")
        prov = (b.get("provenance") or {}).get("aigc") or {}
        if prov.get("label") == "1":
            p["ai_generated"] += 0.15
            ev.append("文件带 AIGC 隐式标识（整片声明含 AI 生成内容，音乐可能也在其中，弱证据）")
        probs = _norm(p)
        lab = max(probs, key=probs.get)
        conf = probs[lab]
        out["music"] = {
            "source": lab if conf >= 0.45 else "unknown", "source_zh": MUSIC_ZH[lab if conf >= 0.45 else "unknown"],
            "leaning": lab, "probs": probs, "ending": ending, "evidence": ev,
            "how_to_confirm": [
                "在抖音/小红书原视频页面看音乐标签：显示歌名 → 现成曲目；显示「@作者创作的原声」→ 作者自己上传（MV 类多为 AI 歌）",
                "用汽水音乐/QQ 音乐「听歌识曲」或 ACRCloud 识别：识别得到就是现成歌曲",
                "听：AI 歌常见咬字含糊、高频发虚带金属感、歌词紧扣画面内容；曲库配乐多为无人声、结构标准、片尾被截断或淡出",
            ],
        }

    # ---------------- voice -------------------------------------------------------------
    if voice.get("present") or nar.get("type") not in (None, "", "none"):
        ev = []
        p = {"tts": 0.3, "ai_video_native": 0.1, "human_recording": 0.3}
        nt = nar.get("type")
        if nt == "tts":
            p["tts"] += 1.0
            ev.append("VLM 判断为 TTS 合成音")
        elif nt == "human":
            p["human_recording"] += 0.8
            ev.append("VLM 判断为真人声音")
        if live_share > 0.5:
            p["human_recording"] += 0.8
            ev.append(f"{live_share:.0%} 的画面是实拍：人声多为同期录音或真人配音")
        if ai_share > 0.4 and clip.get("resets_at_cuts"):
            p["ai_video_native"] += 1.0
            ev.append("背景声在每个切点跳变、画面是 AI 视频：人声很可能是视频模型连同画面一起生成的（如 Seedance/Veo/H3 原生音频）")
        elif clip.get("ratio") is not None and not clip.get("resets_at_cuts") and live_share < 0.3:
            p["tts"] += 0.4
            ev.append("人声和背景声跨切点连续：整条旁白是后期单独配上去的（TTS 或真人配音）")
        if voice.get("f0_cv") is not None and voice["f0_cv"] < 0.12:
            p["tts"] += 0.2
            ev.append(f"音高起伏偏平（变异系数 {voice['f0_cv']}），偏向 TTS（弱证据）")
        probs = _norm(p)
        lab = max(probs, key=probs.get)
        out["voice"] = {"source": lab if probs[lab] >= 0.45 else "unknown", "source_zh": VOICE_ZH[lab if probs[lab] >= 0.45 else "unknown"],
                        "leaning": lab, "probs": probs, "evidence": ev}

    # ---------------- sfx ---------------------------------------------------------------
    sfx = (b.get("audio_plan") or {}).get("sfx_merged", [])
    if sfx or clip.get("resets_at_cuts"):
        ev = []
        p = {"library_editor": 0.4, "ai_native": 0.2, "live_sync": 0.1}
        synced = [e for e in sfx if e.get("sync_with") in ("cut", "text_pop", "transition") or e.get("sync_with_visual")]
        if sfx and len(synced) / len(sfx) >= 0.4:
            p["library_editor"] += 0.8
            ev.append(f"{len(synced)}/{len(sfx)} 个音效踩在切镜/文字弹出/转场上：剪辑时从音效库添加")
        if clip.get("resets_at_cuts") and ai_share > 0.4:
            p["ai_native"] += 1.0
            ev.append(f"背景声/环境声在切点跳变（切点 {clip.get('at_cuts_db')}dB vs 镜头内 {clip.get('within_shots_db')}dB），且画面是 AI 视频：每段自带的音效/环境声是视频模型原生生成的")
        if live_share > 0.5:
            p["live_sync"] += 1.0
            ev.append("画面以实拍为主：环境声多为同期声")
        if b["source"]["path"] and any(l == "cg_3d" for l in labels) and sfx and not clip.get("resets_at_cuts"):
            ev.append("3D/动画类视频的卡通音效也常由程序合成或音效库添加（例如 capsule-cinema plush 配方用程序合成）")
        probs = _norm(p)
        lab = max(probs, key=probs.get)
        out["sfx"] = {"source": lab if probs[lab] >= 0.45 else "unknown", "source_zh": SFX_ZH[lab if probs[lab] >= 0.45 else "unknown"],
                      "leaning": lab, "probs": probs, "evidence": ev}

    # ---------------- one-line answer: video type -> where each sound comes from ---------------
    vt = video_type(ctx, voice, music, clip, ai_share, live_share)
    ctx["video_type"], ctx["video_type_zh"] = vt, VIDEO_TYPE_ZH[vt]
    parts = []
    for k, name in (("voice", "人声"), ("music", "音乐"), ("sfx", "音效")):
        v = out.get(k)
        if v:
            z = {"voice": VOICE_ZH, "music": MUSIC_ZH, "sfx": SFX_ZH}[k][v["leaning"]]
            parts.append(f"{name}{z}" + ("" if v["source"] != "unknown" and v["probs"][v["leaning"]] >= 0.6 else "（倾向，待确认）"))
    out["summary_zh"] = f"视频类型：{VIDEO_TYPE_ZH[vt]} → " + "；".join(parts)
    return out


VIDEO_TYPE_ZH = {"music_mv": "音乐 MV / 歌曲类（整片是一首唱出来的歌）", "ai_native_audio": "AI 视频原生出声（每段画面自带声音）",
                 "live": "实拍（同期声 + 后期配乐）", "narrated": "旁白/口播 + 配乐", "music_bed": "纯配乐（无人声）", "other": "其他"}


def video_type(ctx: dict, voice: dict, music: dict, clip: dict, ai_share: float, live_share: float) -> str:
    if ctx.get("music_mv") or ctx.get("music_mv_maybe"):
        return "music_mv"
    if clip.get("resets_at_cuts") and ai_share > 0.4:
        return "ai_native_audio"
    if live_share > 0.5:
        return "live"
    if voice.get("present"):
        return "narrated"
    if music.get("present"):
        return "music_bed"
    return "other"
