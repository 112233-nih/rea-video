#!/usr/bin/env python3
"""校验 rea-video 能力地图，并可生成能力矩阵。

用法:
  python3 scripts/validate_capabilities.py            # 只校验
  python3 scripts/validate_capabilities.py --matrix   # 校验并写出 capabilities/MATRIX.md

检查项:
  1. 每个模型条目符合 capabilities/schema.json
  2. 模型 id 全局唯一；primitives 引用都存在
  3. 每个原语至少有一个可用（非 shutdown）模型，分析/渲染类除外的空缺会报 warning
  4. routing.yaml 中 signals/route/prefer/verify 引用都存在
  5. prefer 中的每个模型至少支持 route 链中的一个原语
  6. verified.date 超过 90 天给 warning（提示需要复核）
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import yaml
try:
    from jsonschema import Draft202012Validator as SchemaValidator
except ImportError:  # 旧版 jsonschema
    from jsonschema import Draft7Validator as SchemaValidator

ROOT = Path(__file__).resolve().parent.parent
CAP = ROOT / "capabilities"
STALE_DAYS = 90


def _normalize(x):
    """YAML 会把 2026-10-29 解析成 date，这里统一转回字符串。"""
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    if isinstance(x, dict):
        return {k: _normalize(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_normalize(v) for v in x]
    return x


def load_yaml(p: Path):
    with p.open(encoding="utf-8") as f:
        return _normalize(yaml.safe_load(f))


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    schema = json.loads((CAP / "schema.json").read_text(encoding="utf-8"))
    validator = SchemaValidator(schema)

    prim_doc = load_yaml(CAP / "primitives.yaml")
    metrics = set(prim_doc["metrics"].keys())
    primitives = {p["id"]: p for p in prim_doc["primitives"]}
    if len(primitives) != len(prim_doc["primitives"]):
        errors.append("primitives.yaml 中存在重复的原语 id")
    for p in prim_doc["primitives"]:
        for m in p.get("metrics", []):
            if m not in metrics:
                errors.append(f"原语 {p['id']} 引用了未定义的指标 {m}")

    models: dict[str, dict] = {}
    model_file: dict[str, str] = {}
    for f in sorted((CAP / "models").glob("*.yaml")):
        doc = load_yaml(f) or {}
        for m in doc.get("models", []):
            mid = m.get("id", "<missing id>")
            for e in validator.iter_errors(m):
                loc = "/".join(str(x) for x in e.absolute_path)
                errors.append(f"{f.name}:{mid}: schema 错误 {loc}: {e.message}")
            if mid in models:
                errors.append(f"模型 id 重复: {mid}（{model_file[mid]} 与 {f.name}）")
            models[mid] = m
            model_file[mid] = f.name
            for pid in m.get("primitives", []):
                if pid not in primitives:
                    errors.append(f"{f.name}:{mid}: 未知原语 {pid}")
            d = m.get("verified", {}).get("date")
            if d:
                age = (dt.date.today() - dt.date.fromisoformat(d)).days
                if age > STALE_DAYS:
                    warnings.append(f"{mid}: 验证日期 {d} 已超过 {STALE_DAYS} 天，需要复核")

    live = {k: v for k, v in models.items() if v.get("status") != "shutdown"}
    by_prim: dict[str, list[str]] = {p: [] for p in primitives}
    for mid, m in live.items():
        for pid in m.get("primitives", []):
            if pid in by_prim:
                by_prim[pid].append(mid)
    for pid, ms in by_prim.items():
        if not ms:
            warnings.append(f"原语 {pid} 目前没有可用模型")

    routing = load_yaml(CAP / "routing.yaml")
    signals = set(routing.get("signals", {}).keys())
    for sname, s in routing.get("signals", {}).items():
        for pid in s.get("detected_by", []):
            if pid not in primitives:
                errors.append(f"signal {sname}: 未知原语 {pid}")

    def check_rule(r: dict, kind: str) -> None:
        rid = r.get("id", "<no id>")
        for s in (r.get("when") if isinstance(r.get("when"), list) else []):
            if s not in signals:
                errors.append(f"{kind} {rid}: 未知 signal {s}")
        route = r.get("route", [])
        for pid in route:
            if pid not in primitives:
                errors.append(f"{kind} {rid}: 未知原语 {pid}")
        for mid in r.get("prefer", []):
            if mid not in models:
                errors.append(f"{kind} {rid}: 未知模型 {mid}")
                continue
            if models[mid].get("status") == "shutdown":
                errors.append(f"{kind} {rid}: 引用了已关停模型 {mid}")
            if not set(models[mid]["primitives"]) & set(route):
                errors.append(f"{kind} {rid}: 模型 {mid} 不支持 route 中任何原语 {route}")
        for met in r.get("verify", []):
            if met not in metrics:
                errors.append(f"{kind} {rid}: 未知指标 {met}")
        for pid in route:
            if pid in by_prim and not by_prim[pid]:
                errors.append(f"{kind} {rid}: 原语 {pid} 没有任何可用模型")

    for key in ("rules", "audio_rules", "post_rules"):
        ids = [r.get("id") for r in routing.get(key, [])]
        if len(ids) != len(set(ids)):
            errors.append(f"routing.{key} 中存在重复 id")
        for r in routing.get(key, []):
            check_rule(r, key)

    # ---------- 输出 ----------
    print(f"原语 {len(primitives)} 个，指标 {len(metrics)} 个，模型 {len(models)} 个（可用 {len(live)}），"
          f"画面规则 {len(routing.get('rules', []))} 条，音频规则 {len(routing.get('audio_rules', []))} 条")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")

    if "--matrix" in sys.argv and not errors:
        write_matrix(primitives, models, by_prim)
        print("已写出 capabilities/MATRIX.md")

    if errors:
        print(f"\n校验失败：{len(errors)} 个错误")
        return 1
    print("\n校验通过")
    return 0


def write_matrix(primitives: dict, models: dict, by_prim: dict) -> None:
    conf = {"high": "●", "medium": "◐", "low": "○"}
    lines = [
        "# 能力矩阵（自动生成，请勿手改）",
        "",
        "由 `python3 scripts/validate_capabilities.py --matrix` 根据 `capabilities/` 生成。",
        "置信度：● high ◐ medium ○ low。已关停的模型不列出。",
        "",
        "| 原语 | 名称 | 可用模型 |",
        "|---|---|---|",
    ]
    for pid, p in primitives.items():
        ms = sorted(by_prim[pid])
        cell = "、".join(f"{conf[models[m]['confidence']]} `{m}`" for m in ms) or "—（空缺）"
        lines.append(f"| `{pid}` | {p['name']} | {cell} |")
    lines += ["", "## 模型一览", "", "| 模型 | 厂商 | 类别 | 状态 | 时长上限(s) | 原生音频 | 商用 | 验证依据 |", "|---|---|---|---|---|---|---|---|"]
    for mid, m in sorted(models.items(), key=lambda kv: (kv[1]["category"], kv[0])):
        dur = (m.get("limits", {}).get("duration_s") or {}).get("max", "")
        aud = {True: "是", False: "否"}.get((m.get("audio") or {}).get("native"), "")
        com = {True: "是", False: "否", "conditional": "有条件"}[m["license"]["commercial"]]
        lines.append(f"| {m['name']} | {m['vendor']} | {m['category']} | {m['status']} | {dur} | {aud} | {com} | {m['verified']['basis']} {m['verified']['date']} |")
    (CAP / "MATRIX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
