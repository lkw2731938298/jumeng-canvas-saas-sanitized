"""Agent Team Orchestrator · Phase 2 自由创作最小闭环。

流程：art_director → scriptwriter → character_designer → scene_creator → animator(计划镜头) → editor
权威写入 Project Graph；生成媒体不自动扣轨 G（镜头 status=planned，由用户在画布触发生成）。
编排 LLM 走 chat_completion（轨 S 计费后续接入）；无密钥 / mock 时用模板兜底。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..integrations.llm.chat import chat_completion
from ..services.agent_vision_refs import (
    build_controller_user_content,
    collect_vision_image_urls,
    pick_vision_controller_model,
)
from ..models.agent_session import AgentSession
from ..models.user import User
from ..services import agent_sessions, project_graphs
from ..services.agent_controller import get_session_controller_model
from ..services.agent_default_models import (
    AGENT_DEFAULT_IMAGE_T2I,
    AGENT_DEFAULT_MUSIC_MODEL,
    agent_audio_node_params,
    agent_image_node_params,
)

logger = logging.getLogger(__name__)

_EXPAND_SYSTEM = """你是聚梦画布 Agent Team 的剧本策划。根据用户一句话创意，输出严格 JSON（不要 Markdown 代码围栏），结构：
{
  "title": "短片标题",
  "style": {"labels": ["anime"|"cinematic"|"3d"|"watercolor", ...], "aspectRatio": "9:16"|"16:9", "paletteNotes": "色调说明"},
  "beats": [{"id":"b1","summary":"场次概要","dialogue":"可选对白"}],
  "characters": [{"id":"c1","name":"角色名","traits":{"look":"外形","personality":"性格"}}],
  "scenes": [{"id":"s1","name":"场景名","meta":{"timeOfDay":"day|night","mood":"氛围","props":["道具"]}}],
  "shots": [{"id":"sh1","beatId":"b1","characterIds":["c1"],"sceneId":"s1","action":"镜头动作描述","imagePrompt":"英文或中文画面提示词"}]
}
要求：beats 3～6 个；characters 1～4；scenes 1～4；shots 与 beats 对齐，每 beat 至少 1 镜；短片总时长感约 15～45 秒。
若附带参考图：角色外形/场景/镜头 imagePrompt 必须对齐画面主体与风格，勿忽略附图。"""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _strip_json_fence(text: str) -> str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _parse_json_obj(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_strip_json_fence(text))
        return data if isinstance(data, dict) else None
    except Exception:
        # 尝试截取首尾花括号
        m = re.search(r"\{[\s\S]*\}", text or "")
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except Exception:
            return None


def _template_expand(idea: str) -> dict[str, Any]:
    """无 LLM 时的确定性短漫剧骨架。"""
    title = (idea[:24] + "…") if len(idea) > 24 else (idea or "未命名短片")
    c1, c2 = _new_id("c"), _new_id("c")
    s1, s2 = _new_id("s"), _new_id("s")
    b1, b2, b3 = _new_id("b"), _new_id("b"), _new_id("b")
    sh1, sh2, sh3 = _new_id("sh"), _new_id("sh"), _new_id("sh")
    return {
        "title": title,
        "style": {
            "labels": ["cinematic"],
            "aspectRatio": "9:16",
            "paletteNotes": "温暖对比、电影感光影",
        },
        "beats": [
            {"id": b1, "summary": f"开场：引出「{title}」的世界与主角", "dialogue": ""},
            {"id": b2, "summary": "冲突升级：主角面对关键阻碍", "dialogue": ""},
            {"id": b3, "summary": "收束：情绪高潮与余韵", "dialogue": ""},
        ],
        "characters": [
            {
                "id": c1,
                "name": "主角",
                "traits": {"look": "鲜明剪影、易识别", "personality": "勇敢好奇"},
            },
            {
                "id": c2,
                "name": "配角",
                "traits": {"look": "与主角形成对比", "personality": "机智或对立"},
            },
        ],
        "scenes": [
            {"id": s1, "name": "起始场景", "meta": {"timeOfDay": "day", "mood": "建立", "props": []}},
            {"id": s2, "name": "高潮场景", "meta": {"timeOfDay": "dusk", "mood": "紧张", "props": []}},
        ],
        "shots": [
            {
                "id": sh1,
                "beatId": b1,
                "characterIds": [c1],
                "sceneId": s1,
                "action": "建立镜头，展示环境与主角登场",
                "imagePrompt": f"{idea}, establishing shot, cinematic lighting",
            },
            {
                "id": sh2,
                "beatId": b2,
                "characterIds": [c1, c2],
                "sceneId": s2,
                "action": "冲突对峙或追逐",
                "imagePrompt": f"{idea}, conflict moment, dynamic composition",
            },
            {
                "id": sh3,
                "beatId": b3,
                "characterIds": [c1],
                "sceneId": s2,
                "action": "情绪收束特写",
                "imagePrompt": f"{idea}, emotional close-up, soft light",
            },
        ],
    }


async def _llm_expand(
    idea: str,
    *,
    preferred_model: str | None = None,
    image_urls: list[str] | None = None,
) -> dict[str, Any]:
    model = pick_vision_controller_model(
        preferred_model, has_images=bool(image_urls)
    )
    if not model:
        logger.info("agent_team: no controller model / mock → template expand")
        return _template_expand(idea)
    try:
        user_content = build_controller_user_content(
            f"用户创意：{idea}", model_id=model, image_urls=image_urls
        )
        content = await chat_completion(
            model,
            [
                {"role": "system", "content": _EXPAND_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            temperature=0.7,
            max_tokens=4096,
            timeout_s=120.0,
        )
        parsed = _parse_json_obj(content)
        if not parsed:
            logger.warning("agent_team: LLM JSON parse failed, fallback template")
            return _template_expand(idea)
        return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent_team: LLM expand failed: %s", exc)
        return _template_expand(idea)


def _normalize_expanded(
    raw: dict[str, Any],
    idea: str,
    *,
    style_id: str | None = None,
    ref_asset_ids: list[str] | None = None,
    preferred_aspect: str | None = None,
    preferred_duration: str | None = None,
    preferred_image_model: str | None = None,
    preferred_video_model: str | None = None,
    preferred_clarity: str | None = None,
) -> dict[str, Any]:
    title = str(raw.get("title") or idea[:40] or "未命名短片").strip()
    # 首页/会话附件参考图 → 写入首个角色 identityLock
    refs = [
        str(x).strip()
        for x in (ref_asset_ids or [])
        if str(x).strip()
    ][:12]
    style_in = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    labels = list(style_in.get("labels") or ["cinematic"])[:5]
    # 用户选定 visual_style 时写入 Graph，并尝试用平台风格名充实 labels
    resolved_style_id = (style_id or "").strip() or None
    if resolved_style_id in ("none", ""):
        resolved_style_id = None
    style_label = ""
    if resolved_style_id:
        try:
            from .visual_style import list_visual_styles

            for item in list_visual_styles():
                if item.get("id") == resolved_style_id:
                    style_label = str(item.get("label") or resolved_style_id)
                    break
        except Exception:  # noqa: BLE001
            style_label = resolved_style_id
        if style_label and style_label not in labels:
            labels = [style_label, *labels][:5]
    # 我的 Skill / brief 指定画幅优先于 LLM 随意发挥
    aspect = (
        str(preferred_aspect or "").strip()
        or str(style_in.get("aspectRatio") or "").strip()
        or "9:16"
    )
    style = {
        "styleId": resolved_style_id,
        "labels": labels,
        "aspectRatio": aspect,
        "paletteNotes": str(style_in.get("paletteNotes") or ""),
    }
    if preferred_duration:
        style["durationSec"] = str(preferred_duration).strip()
    if preferred_clarity:
        style["clarity"] = str(preferred_clarity).strip()
    if preferred_image_model:
        style["imageModel"] = str(preferred_image_model).strip()
    if preferred_video_model:
        style["videoModel"] = str(preferred_video_model).strip()

    beats = []
    for i, b in enumerate(raw.get("beats") or []):
        if not isinstance(b, dict):
            continue
        beats.append(
            {
                "id": str(b.get("id") or _new_id("b")),
                "summary": str(b.get("summary") or f"场次 {i+1}"),
                "dialogue": str(b.get("dialogue") or ""),
            }
        )
    if not beats:
        beats = [{"id": _new_id("b"), "summary": idea or "开场", "dialogue": ""}]

    characters = []
    for i, c in enumerate(raw.get("characters") or []):
        if not isinstance(c, dict):
            continue
        traits = c.get("traits") if isinstance(c.get("traits"), dict) else {}
        characters.append(
            {
                "id": str(c.get("id") or _new_id("c")),
                "name": str(c.get("name") or "角色"),
                "identityLock": {
                    "refAssetIds": list(refs) if i == 0 and refs else [],
                    "sheetAssetIds": [],
                },
                "traits": {str(k): str(v) for k, v in traits.items()},
            }
        )
    # 有参考图但 LLM 未产出角色：仍建占位角色承载 identityLock
    if refs and not characters:
        characters.append(
            {
                "id": _new_id("c"),
                "name": "参考角色",
                "identityLock": {"refAssetIds": list(refs), "sheetAssetIds": []},
                "traits": {},
            }
        )

    scenes = []
    for s in raw.get("scenes") or []:
        if not isinstance(s, dict):
            continue
        meta = s.get("meta") if isinstance(s.get("meta"), dict) else {}
        scenes.append(
            {
                "id": str(s.get("id") or _new_id("s")),
                "name": str(s.get("name") or "场景"),
                "refAssetIds": [],
                "meta": {
                    "timeOfDay": str(meta.get("timeOfDay") or ""),
                    "mood": str(meta.get("mood") or ""),
                    "props": list(meta.get("props") or [])[:8],
                },
            }
        )

    shots = []
    for i, sh in enumerate(raw.get("shots") or []):
        if not isinstance(sh, dict):
            continue
        action = str(sh.get("action") or sh.get("imagePrompt") or f"镜头 {i+1}")
        prompt = str(sh.get("imagePrompt") or action)
        img_model = str(preferred_image_model or "").strip() or AGENT_DEFAULT_IMAGE_T2I
        shots.append(
            {
                "id": str(sh.get("id") or _new_id("sh")),
                "beatId": str(sh.get("beatId") or (beats[min(i, len(beats) - 1)]["id"])),
                "characterIds": [str(x) for x in (sh.get("characterIds") or [])],
                "sceneId": str(sh.get("sceneId") or (scenes[0]["id"] if scenes else "")),
                "action": action,
                "preferredModel": img_model,
                "status": "planned",
                "outputAssetId": None,
                "canvasNodeId": None,
                "imagePrompt": prompt,
            }
        )
    if not shots and beats:
        img_model = str(preferred_image_model or "").strip() or AGENT_DEFAULT_IMAGE_T2I
        shots.append(
            {
                "id": _new_id("sh"),
                "beatId": beats[0]["id"],
                "characterIds": [characters[0]["id"]] if characters else [],
                "sceneId": scenes[0]["id"] if scenes else "",
                "action": beats[0]["summary"],
                "preferredModel": img_model,
                "status": "planned",
                "outputAssetId": None,
                "canvasNodeId": None,
                "imagePrompt": beats[0]["summary"],
            }
        )

    return {
        "title": title,
        "style": style,
        "beats": beats,
        "characters": characters,
        "scenes": scenes,
        "shots": shots,
    }


def _build_canvas_ops(
    norm: dict[str, Any],
    *,
    media_kind: str = "video",
    preferred_image_model: str | None = None,
) -> list[dict[str, Any]]:
    """生成前端 Projector 可执行的画布操作（按 mediaKind 裁剪节点）。"""
    kind = str(media_kind or "video").strip().lower()
    if kind not in ("image", "video", "text", "audio"):
        kind = "video"
    ops: list[dict[str, Any]] = []
    x0, y0 = 80, 80
    gap_x, gap_y = 300, 220

    script_lines = [f"# {norm['title']}", ""]
    for i, b in enumerate(norm["beats"]):
        script_lines.append(f"## {i+1}. {b['summary']}")
        if b.get("dialogue"):
            script_lines.append(f"> {b['dialogue']}")
        script_lines.append("")
    ops.append(
        {
            "op": "add_text_node",
            "tempId": "script",
            "label": "剧本" if kind != "text" else "文案 / 剧本",
            "content": "\n".join(script_lines).strip(),
            "x": x0,
            "y": y0,
            "params": {"textPromptKind": "text_script"},
        }
    )

    char_sheet_temp_ids: dict[str, str] = {}
    if kind != "audio":
        char_lines = ["# 角色设定", ""]
        for c in norm["characters"]:
            traits = c.get("traits") or {}
            trait_s = "；".join(f"{k}:{v}" for k, v in traits.items())
            char_lines.append(f"- **{c['name']}**：{trait_s or '待完善'}")
            char_lines.append("")
        ops.append(
            {
                "op": "add_text_node",
                "tempId": "characters",
                "label": "角色",
                "content": "\n".join(char_lines).strip(),
                "x": x0 + gap_x,
                "y": y0,
                "params": {"textPromptKind": "text_subject"},
            }
        )

        # identityLock 三视图闭环第一步：每个角色建一张「定妆图」节点（characterId
        # 绑定，params.characterAssetRole="sheet"）。用户手动生成后，前端
        # reflowCharacterSheetToProjectGraph 会把产物 assetId 回写
        # characters[].identityLock.sheetAssetIds；下方再把该节点连到各镜头节点
        # 上游，复用「自动选取上游连线媒体作为参考」的既有通道锁定角色形象，不新增
        # 生成提交逻辑，也不自动扣算力。
        if kind in ("image", "video"):
            for i, c in enumerate(norm["characters"][:6]):
                traits = c.get("traits") or {}
                look = str(traits.get("look") or traits.get("外形") or "").strip()
                personality = str(
                    traits.get("personality") or traits.get("性格") or ""
                ).strip()
                sheet_prompt = (
                    f"角色三视图设定图：{c['name']}"
                    + (f"，外形：{look}" if look else "")
                    + (f"，性格气质：{personality}" if personality else "")
                    + "。正面、侧面、背面三视图并排展示，身高比例、服装与配饰保持一致，"
                    "站姿端正，背景简洁，适合角色定妆与后续镜头参考。"
                )
                sheet_temp_id = f"char_sheet_{c['id']}"
                char_sheet_temp_ids[str(c["id"])] = sheet_temp_id
                ops.append(
                    {
                        "op": "add_image_node",
                        "tempId": sheet_temp_id,
                        "label": f"角色定妆图·{c['name']}",
                        "prompt": sheet_prompt,
                        "x": x0 + gap_x * 3,
                        "y": y0 + i * 140,
                        "characterId": str(c["id"]),
                        "params": agent_image_node_params({"characterAssetRole": "sheet"}),
                    }
                )
                ops.append(
                    {
                        "op": "connect_nodes",
                        "source": "characters",
                        "target": sheet_temp_id,
                        "sourceHandle": "text",
                        "targetHandle": "ref_in",
                    }
                )

    if kind in ("image", "video"):
        scene_lines = ["# 场景", ""]
        for s in norm["scenes"]:
            meta = s.get("meta") or {}
            scene_lines.append(
                f"- **{s['name']}**（{meta.get('timeOfDay','')} / {meta.get('mood','')}）"
            )
        ops.append(
            {
                "op": "add_text_node",
                "tempId": "scenes",
                "label": "场景",
                "content": "\n".join(scene_lines).strip(),
                "x": x0 + gap_x * 2,
                "y": y0,
            }
        )

    style = norm.get("style") if isinstance(norm.get("style"), dict) else {}
    # 我的 Skill 默认：生图模型 + 画幅/清晰度/时长写入节点 generationOptions
    gen_opts: dict[str, str] = {}
    if style.get("aspectRatio"):
        gen_opts["ratio"] = str(style["aspectRatio"])
    if style.get("clarity"):
        gen_opts["resolution"] = str(style["clarity"])
    if style.get("durationSec") and kind == "video":
        gen_opts["duration"] = str(style["durationSec"])
    # 画布操控选择器 > Skill/风格默认 > 内置兜底
    pref_img = (
        str(preferred_image_model or "").strip()
        or str(style.get("imageModel") or "").strip()
    )

    shot_temp_ids: list[str] = []
    if kind in ("image", "video"):
        for i, sh in enumerate(norm["shots"][:12]):
            col, row = i % 4, i // 4
            prompt = sh.get("imagePrompt") or sh.get("action") or ""
            extra: dict[str, Any] = {}
            if pref_img:
                extra["model"] = pref_img
            if gen_opts:
                extra["generationOptions"] = dict(gen_opts)
            # 镜头节点带上风格 id + 用户选定/默认生图模型
            params: dict[str, Any] = agent_image_node_params(extra or None)
            if style.get("styleId"):
                params["visualStyleId"] = style["styleId"]
            # 节点 id = shot id，便于连线与续聊引用
            sh["canvasNodeId"] = sh["id"]
            sh["preferredModel"] = str(params.get("model") or AGENT_DEFAULT_IMAGE_T2I)
            shot_temp_ids.append(str(sh["id"]))
            shot_label = f"分镜{i+1}" if kind == "image" else f"镜头{i+1}"
            ops.append(
                {
                    "op": "add_image_node",
                    "tempId": sh["id"],
                    "label": shot_label,
                    "prompt": prompt,
                    "x": x0 + col * gap_x,
                    "y": y0 + gap_y + row * gap_y,
                    "shotId": sh["id"],
                    "params": params,
                }
            )

    # 默认连线：剧本/角色 → 各镜头图（不自动 generate，避免未确认扣轨 G）
    for sid in shot_temp_ids:
        ops.append(
            {
                "op": "connect_nodes",
                "source": "script",
                "target": sid,
                "sourceHandle": "text",
                "targetHandle": "ref_in",
            }
        )
        if kind != "audio":
            ops.append(
                {
                    "op": "connect_nodes",
                    "source": "characters",
                    "target": sid,
                    "sourceHandle": "text",
                    "targetHandle": "ref_in",
                }
            )

    # identityLock 闭环第二步：镜头 → 角色定妆图上游连线。定妆图此时可能还未生成
    # （用户尚未手动点生成），连线仍先建立；等用户后续生成定妆图、再手动生成该镜头时，
    # collectGenerationReferences 的「自动选取上游连线媒体」通道会自动带上该参考图。
    if char_sheet_temp_ids:
        for sh in norm["shots"][:12]:
            target_id = str(sh.get("id") or "")
            if not target_id or target_id not in shot_temp_ids:
                continue
            for cid in sh.get("characterIds") or []:
                sheet_temp_id = char_sheet_temp_ids.get(str(cid))
                if not sheet_temp_id:
                    continue
                ops.append(
                    {
                        "op": "connect_nodes",
                        "source": sheet_temp_id,
                        "target": target_id,
                        "sourceHandle": "image",
                        "targetHandle": "ref_in",
                    }
                )

    # 音效 Agent：文本方案 + 音频节点占位（用户手动生成，不自动扣轨 G）。
    # BGM/SFX 节点均带默认音乐模型（系统暂无专用音效模型，音效节点复用音乐模型生成
    # 氛围音效），避免用户手动点生成时退化到默认配音模型（cosyvoice_tts）产出驴唇不对马嘴。
    audio = norm.get("audio") if isinstance(norm.get("audio"), dict) else {}
    if audio:
        sfx_list = [s for s in (audio.get("sfx") or []) if isinstance(s, dict)][:3]
        sfx_lines = ["# 音效 / BGM 方案", ""]
        bgm = audio.get("bgm") if isinstance(audio.get("bgm"), dict) else {}
        if bgm:
            sfx_lines.append(f"- **BGM**：{bgm.get('prompt') or bgm.get('mood') or '待定'}")
        for i, sfx in enumerate(sfx_list):
            sfx_lines.append(f"- **SFX{i+1}**：{sfx.get('prompt') or sfx.get('cue') or ''}")
        sfx_lines.append("")
        sfx_lines.append(
            f"> 暂无专用音效模型，音效节点已用音乐模型（{AGENT_DEFAULT_MUSIC_MODEL}）"
            "生成氛围音效占位，可手动换模型；均需手动点「生成」，不自动扣算力。"
        )
        ops.append(
            {
                "op": "add_text_node",
                "tempId": "audio_plan",
                "label": "音效方案",
                "content": "\n".join(sfx_lines).strip(),
                "x": x0,
                "y": y0 + gap_y * 3,
            }
        )
        ops.append(
            {
                "op": "add_audio_node",
                "tempId": "bgm",
                "label": "BGM",
                "prompt": str((bgm or {}).get("prompt") or "cinematic background music, soft underscore"),
                "x": x0 + gap_x,
                "y": y0 + gap_y * 3,
                "params": agent_audio_node_params({"generationMode": "music"}),
            }
        )
        ops.append(
            {
                "op": "connect_nodes",
                "source": "audio_plan",
                "target": "bgm",
                "sourceHandle": "text",
                "targetHandle": "ref_in",
            }
        )
        for i, sfx in enumerate(sfx_list):
            sfx_temp_id = f"sfx_{i}"
            ops.append(
                {
                    "op": "add_audio_node",
                    "tempId": sfx_temp_id,
                    "label": f"音效{i+1}",
                    "prompt": str(sfx.get("prompt") or sfx.get("cue") or "cinematic ambient sound effect"),
                    "x": x0 + gap_x,
                    "y": y0 + gap_y * 3 + (i + 1) * 90,
                    "params": agent_audio_node_params({"generationMode": "music"}),
                }
            )
            ops.append(
                {
                    "op": "connect_nodes",
                    "source": "audio_plan",
                    "target": sfx_temp_id,
                    "sourceHandle": "text",
                    "targetHandle": "ref_in",
                }
            )
    return ops


def _build_audio_plan(norm: dict[str, Any]) -> dict[str, Any]:
    """Sound Engineer：根据剧本/镜头生成 BGM+SFX 计划（不调上游，仅 Graph）。"""
    title = norm.get("title") or "短片"
    mood = ""
    style = norm.get("style") if isinstance(norm.get("style"), dict) else {}
    if style.get("paletteNotes"):
        mood = str(style["paletteNotes"])
    elif style.get("labels"):
        mood = "、".join(str(x) for x in style["labels"][:3])
    beats = norm.get("beats") or []
    sfx: list[dict[str, Any]] = []
    for i, b in enumerate(beats[:6]):
        if not isinstance(b, dict):
            continue
        sfx.append(
            {
                "id": _new_id("sfx"),
                "beatId": b.get("id"),
                "cue": str(b.get("summary") or f"场次{i+1}"),
                "prompt": f"sound effect for: {b.get('summary') or title}, subtle cinematic",
                "status": "planned",
                "outputAssetId": None,
            }
        )
    return {
        "bgm": {
            "id": _new_id("bgm"),
            "mood": mood or "cinematic",
            "prompt": f"background music for short film 「{title}」, {mood or 'warm cinematic'}, loopable",
            "status": "planned",
            "outputAssetId": None,
            "timelineTrack": "bgm",
        },
        "sfx": sfx,
        "timelineHints": {
            "placeBgmOnTrack": "bgm",
            "alignSfxToBeats": True,
            "note": "剪辑页可按 timeline.shotIds 对齐音效；音频节点需用户手动生成",
        },
    }

async def run_free_creation_pipeline(
    db: AsyncSession,
    *,
    session: AgentSession,
    user: User,
    idea: str,
) -> dict[str, Any]:
    """执行自由创作 Agent Team；写 Graph + 消息；返回 graph 摘要。"""
    idea = (idea or "").strip() or "一个温暖的短片故事"
    project_id = int(session.project_id)
    graph_written = False

    try:
        await agent_sessions.append_message(
            db,
            session,
            role="agent",
            agent_role="orchestrator",
            content="Agent Team 启动：导演 → 剧本 → 角色 → 场景 → 镜头计划 → 剪辑骨架 → 音效",
        )

        # 1) Art Director（风格先占位，随后用 LLM 结果覆盖）
        await agent_sessions.append_message(
            db,
            session,
            role="agent",
            agent_role="art_director",
            content="正在确定视觉风格与画幅…",
        )

        # 2) Scriptwriter + 下游（一次 LLM 产出全量，再分角色落库消息）
        await agent_sessions.append_message(
            db,
            session,
            role="agent",
            agent_role="scriptwriter",
            content="正在把想法扩展为分场剧本…",
        )
        chip_style = getattr(session, "style_id", None)
        brief = session.brief_json if isinstance(session.brief_json, dict) else {}
        my_defaults = (
            brief.get("mySkillDefaults")
            if isinstance(brief.get("mySkillDefaults"), dict)
            else {}
        )
        # 将另存约束拼进 idea；非视频不强制时长；文本/生图不配乐
        media_kind = str(my_defaults.get("mediaKind") or "").strip().lower()
        if media_kind not in ("image", "video", "text", "audio"):
            media_kind = "video"
        constraint_bits: list[str] = []
        ar = str(
            my_defaults.get("aspectRatio") or brief.get("aspectRatio") or ""
        ).strip()
        dur = str(
            my_defaults.get("durationSec") or brief.get("durationSec") or ""
        ).strip()
        if media_kind != "video":
            dur = ""
        clarity = str(my_defaults.get("clarity") or brief.get("clarity") or "").strip()
        if media_kind not in ("image", "video"):
            clarity = ""
        if ar and media_kind in ("image", "video"):
            constraint_bits.append(f"画幅 {ar}")
        if dur:
            constraint_bits.append(f"总时长约 {dur} 秒")
        if clarity:
            constraint_bits.append(f"清晰度 {clarity}")
        if media_kind == "image":
            constraint_bits.append("产出为分镜静帧/条漫图片，不要按视频成片或配乐剪辑来规划")
        elif media_kind == "text":
            constraint_bits.append("产出为文案/剧本/台词等文本，不要规划成片剪辑或强制出图")
        elif media_kind == "audio":
            constraint_bits.append("产出侧重配乐/音效方案，不要强行走分镜出图或完整成片剪辑")
        expand_idea = idea
        if constraint_bits:
            expand_idea = f"{idea}\n\n【必须遵守】{'；'.join(constraint_bits)}"
        # 我的 Skill：把节点配方与规格塞进扩写提示，保证格数/镜头数对齐
        recipe = my_defaults.get("nodeRecipe")
        if isinstance(recipe, dict) and recipe.get("nodes"):
            from .skill_node_recipe import (
                normalize_node_recipe,
                recipe_markdown_sections,
            )

            nr = normalize_node_recipe(recipe, media_kind=media_kind)
            nodes_md, order_md, specs_md = recipe_markdown_sections(nr)
            expand_idea += (
                "\n\n【Skill 节点配方——建节点时必须对齐】\n"
                f"{specs_md}\n{nodes_md}\n{order_md}"
            )
        # 平台 Skill：多文件包整包注入；由模型按技能包+用户方案扩写（不强制固定分镜表）
        try:
            if session.skill_id:
                from sqlalchemy import select

                from ..models.skill import Skill
                # 与 runtime 一致：渐进披露，不整包注入 references
                from .skill_docs import format_runtime_skill_context

                skill_row = (
                    await db.execute(
                        select(Skill).where(Skill.id == int(session.skill_id)).limit(1)
                    )
                ).scalar_one_or_none()
                if skill_row is not None:
                    pkg_ctx = await format_runtime_skill_context(
                        db, bound_skill=skill_row
                    )
                    if pkg_ctx:
                        expand_idea += (
                            "\n\n【Skill 配方（目录+正文）——据此自行设计分镜与提示词，"
                            "勿机械套固定镜头表】\n"
                            f"{pkg_ctx}"
                        )
        except Exception:  # noqa: BLE001
            pass

        ref_ids = [
            str(x).strip()
            for x in (brief.get("referenceAssetIds") or [])
            if str(x).strip()
        ]
        # 参考图送视觉模型，扩写角色/镜头时对齐画面（不仅写 identityLock id）
        image_urls = await collect_vision_image_urls(
            db,
            project_id,
            message_text=expand_idea,
            reference_asset_ids=ref_ids,
        )
        raw = await _llm_expand(
            expand_idea,
            preferred_model=get_session_controller_model(session),
            image_urls=image_urls or None,
        )
        # 画布操控选择器（brief）优先于 Skill 默认生图/生视频模型
        pref_img = str(
            brief.get("preferredImageModel") or my_defaults.get("imageModel") or ""
        ).strip() or None
        pref_vid = str(
            brief.get("preferredVideoModel") or my_defaults.get("videoModel") or ""
        ).strip() or None
        if media_kind != "video":
            pref_vid = None
        if media_kind not in ("image", "video"):
            pref_img = None
        norm = _normalize_expanded(
            raw,
            expand_idea,
            style_id=chip_style,
            ref_asset_ids=ref_ids,
            preferred_aspect=ar or None,
            preferred_duration=dur or None,
            preferred_image_model=pref_img,
            preferred_video_model=pref_vid,
            preferred_clarity=clarity or None,
        )
        if ref_ids:
            norm["referenceAssetIds"] = ref_ids
        if media_kind in ("image", "text"):
            # 生图/文本流水线不写入音频计划（空 dict 避免投影音效节点）
            norm["audio"] = {}
        else:
            norm["audio"] = _build_audio_plan(norm)
        audio = norm.get("audio") if isinstance(norm.get("audio"), dict) else {}

        style_msg = (
            f"风格：{', '.join(norm['style']['labels']) or '默认'}；"
            f"画幅 {norm['style']['aspectRatio']}。"
        )
        if norm["style"].get("styleId"):
            style_msg = f"已绑定风格库 `{norm['style']['styleId']}`。" + style_msg
        if norm["style"].get("paletteNotes"):
            style_msg += str(norm["style"]["paletteNotes"])

        await agent_sessions.append_message(
            db,
            session,
            role="agent",
            agent_role="art_director",
            content=style_msg.strip(),
            graph_patch={"style": norm["style"]},
        )

        await agent_sessions.append_message(
            db,
            session,
            role="agent",
            agent_role="scriptwriter",
            content=f"《{norm['title']}》已写出 {len(norm['beats'])} 个场次。",
            graph_patch={"script": {"title": norm["title"], "beats": norm["beats"]}},
        )

        # 音频流水线跳过角色锁定；文本/生图/视频保留
        if media_kind != "audio":
            ref_note = (
                f"已绑定 {len(ref_ids)} 张参考图到身份锁。"
                if ref_ids
                else "身份锁已建立（参考图可后续补充）。"
            )
            await agent_sessions.append_message(
                db,
                session,
                role="agent",
                agent_role="character_designer",
                content=(
                    "角色已锁定："
                    + ("、".join(c["name"] for c in norm["characters"]) or "（无）")
                    + f"。{ref_note}"
                ),
                graph_patch={"characters": norm["characters"]},
            )

        # 文本/音频流水线不建场景板；生图/视频保留
        if media_kind in ("image", "video"):
            await agent_sessions.append_message(
                db,
                session,
                role="agent",
                agent_role="scene_creator",
                content=(
                    "场景板："
                    + ("、".join(s["name"] for s in norm["scenes"]) or "（无）")
                ),
                graph_patch={"scenes": norm["scenes"]},
            )

        shot_ids = [s["id"] for s in norm["shots"]]
        # 文本/音频不出分镜；生图用「分镜」文案
        if media_kind in ("image", "video"):
            shot_word = "分镜" if media_kind == "image" else "镜头"
            await agent_sessions.append_message(
                db,
                session,
                role="agent",
                agent_role="animator",
                content=(
                    f"已规划 {len(norm['shots'])} 个{shot_word}（status=planned）。"
                    "画面提示词已写入 Graph；请在画布节点上手动点生成（不自动扣生成算力）。"
                ),
                graph_patch={
                    "shots": [
                        {"id": s["id"], "status": s["status"]} for s in norm["shots"]
                    ]
                },
            )

        timeline = {
            "shotIds": shot_ids if media_kind in ("image", "video") else [],
            "transitions": [],
            "audio": {
                "bgmId": audio.get("bgm", {}).get("id")
                if media_kind in ("video", "audio")
                else None,
                "sfxIds": (
                    [x.get("id") for x in (audio.get("sfx") or []) if isinstance(x, dict)]
                    if media_kind in ("video", "audio")
                    else []
                ),
                "hints": audio.get("timelineHints") or {},
            },
        }
        # 短视频：剪辑 + 配乐；音频 Skill：仅配乐；生图/文本：均跳过
        if media_kind == "video":
            await agent_sessions.append_message(
                db,
                session,
                role="agent",
                agent_role="editor",
                content=(
                    f"时间线骨架已按 {len(shot_ids)} 镜排序；"
                    "已预留 BGM/SFX 轨道提示。"
                    "各镜视频生成完成后，可说「组装剪辑台」写入多轨草稿并打开剪辑台微调；"
                    "若已有手工草稿需覆盖，再说「强制组装剪辑台」。"
                ),
                graph_patch={"timeline": timeline},
            )

        if media_kind in ("video", "audio"):
            await agent_sessions.append_message(
                db,
                session,
                role="agent",
                agent_role="sound_engineer",
                content=(
                    f"音效方案：BGM「{audio.get('bgm', {}).get('mood') or 'cinematic'}」+ "
                    f"{len(audio.get('sfx') or [])} 条 SFX（status=planned）。"
                    "已投影音效文本与 BGM 音频节点；请手动生成后挂到时间线。"
                ),
                graph_patch={"audio": audio},
            )

        # 文本流水线仍保留角色；音频跳过角色设计消息（上游可能已写 identity）
        graph = project_graphs.empty_graph(project_id)
        graph.update(
            {
                "style": norm["style"],
                "characters": norm["characters"] if media_kind != "audio" else [],
                "scenes": norm["scenes"] if media_kind in ("image", "video") else [],
                "script": {"title": norm["title"], "beats": norm["beats"]},
                "shots": norm["shots"] if media_kind in ("image", "video") else [],
                "timeline": timeline if media_kind == "video" else {"shotIds": shot_ids if media_kind == "image" else []},
                "audio": audio if media_kind in ("video", "audio") else {},
            }
        )
        canvas_ops = _build_canvas_ops(
            norm,
            media_kind=media_kind,
            preferred_image_model=pref_img,
        )
        saved = await project_graphs.save_graph(
            db, project_id, graph, canvas_ops=canvas_ops, bump_revision=True
        )
        graph_written = True

        from .agent_session_credits import commit_orchestration_credits

        await commit_orchestration_credits(db, session)

        session.status = "completed"
        # 标记进入「等用户确认生成」阶段（我的 Skill）
        if isinstance(my_defaults, dict) and my_defaults:
            brief2 = dict(brief) if isinstance(brief, dict) else {}
            brief2["mySkillPhase"] = "awaiting_generate_confirm"
            session.brief_json = brief2
        gen_hint = {
            "image": "出图 / 全部生成 / 开始生成",
            "video": "出视频 / 全部生成 / 开始生成",
            "audio": "生成音频 / 全部生成 / 开始生成",
            "text": "生成文本 / 全部生成 / 开始生成",
        }.get(media_kind, "全部生成 / 开始生成")
        await agent_sessions.append_message(
            db,
            session,
            role="assistant",
            agent_role="orchestrator",
            content=(
                f"编排完成：《{norm['title']}》。"
                "节点与连线、提示词/正文已落到画布。"
                f"是否现在开始生成？你可以说「{gen_hint}」，我会按模型扣算力执行（与手点同价）；"
                "也可以先改节点内容，或说：加镜头、改风格、改剧本/角色。"
                + (
                    "各镜成片后还可说「组装剪辑台」进入剪辑台。"
                    if media_kind == "video"
                    else ""
                )
            ),
            graph_patch={"revision": saved.get("revision")},
        )
        return saved
    except Exception:
        # 退款由 run_session_team_job 在 rollback 后的独立会话执行（避免本事务回滚冲掉退款）
        session._agent_graph_written = graph_written  # type: ignore[attr-defined]
        raise


async def run_session_team_job(session_id: int, idea: str) -> None:
    """后台任务入口：独立 DB session。"""
    from ..models.database import async_session
    from sqlalchemy import select
    from ..models.user import User as UserModel

    async with async_session() as db:
        graph_written = False
        session: AgentSession | None = None
        try:
            session = (
                await db.execute(
                    select(AgentSession).where(AgentSession.id == int(session_id)).limit(1)
                )
            ).scalar_one_or_none()
            if session is None:
                return
            user = (
                await db.execute(
                    select(UserModel).where(UserModel.id == int(session.user_id)).limit(1)
                )
            ).scalar_one_or_none()
            if user is None:
                return
            await run_free_creation_pipeline(db, session=session, user=user, idea=idea)
            await db.commit()
        except Exception as exc:  # noqa: BLE001
            if session is not None:
                graph_written = bool(getattr(session, "_agent_graph_written", False))
            await db.rollback()
            logger.exception("agent_team session %s failed: %s", session_id, exc)
            async with async_session() as db2:
                try:
                    session2 = (
                        await db2.execute(
                            select(AgentSession)
                            .where(AgentSession.id == int(session_id))
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if session2 is not None:
                        from .agent_session_credits import (
                            commit_orchestration_credits,
                            release_orchestration_credits,
                        )

                        if graph_written:
                            await commit_orchestration_credits(db2, session2)
                        else:
                            await release_orchestration_credits(db2, session2)
                        session2.status = "failed"
                        await agent_sessions.append_message(
                            db2,
                            session2,
                            role="assistant",
                            agent_role="orchestrator",
                            content=f"Agent Team 编排失败：{exc}",
                        )
                        await db2.commit()
                except Exception:  # noqa: BLE001
                    await db2.rollback()
