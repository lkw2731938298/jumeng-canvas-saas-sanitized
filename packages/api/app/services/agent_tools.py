"""AI 助手可调用工具：OpenAI schema + 服务端执行 + 客户端工具转 canvasOps。

读类在后端完成；写画布 / 跑工具 / 生成由前端投影执行。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .canvas_tool_models import CANVAS_TOOL_MODEL_IDS

# 前端 runAgentCanvasTool 额外支持（不在模型配置表内）
_EXTRA_RUN_TOOLS: tuple[str, ...] = (
    "grid_split",
    "storyboard_batch_videos",
    "panorama_720",
    "visual_style",
)

CANVAS_RUN_TOOL_IDS: tuple[str, ...] = tuple(
    dict.fromkeys([*CANVAS_TOOL_MODEL_IDS, *_EXTRA_RUN_TOOLS])
)

# list_canvas_tools 速查：前置节点 + 关键 params（完整说明见 agent_canvas_tools.md）
CANVAS_TOOL_NODE_HINTS: dict[str, str] = {
    "storyboard_table": "剧本→分镜表行；前置 storyboard_grid",
    "storyboard_from_image": "从图填表；前置表或有图 image_input",
    "storyboard_from_video": "视频拉片填表；nodeId 可以是有媒体的 video_input（自动建表，等同顶栏解析）或已有分镜表",
    "storyboard_overseas_localize": "出海改文案；前置已有行的表；params.targetMarketId",
    "text_subject": "抽角色/场景/道具；前置表或剧本",
    "storyboard_camera": "补运镜词；前置已有行的表",
    "storyboard_video": "补每镜视频提示词；前置已有行的表",
    "storyboard": "一张多格合成分镜图（不是表格）；前置 image_input；params.userPrompt 必填",
    "blocking_storyboard": "机位/站位合成图；前置 image_input；params.userPrompt 必填",
    "storyboard_sketch": "每镜草图；前置已有行的表",
    "storyboard_subject_image": "主体定妆图；前置已有主体设定",
    "grid_split": "宫格切成本地多图（不调模型）；params.rows/cols 默认 3×3",
    "storyboard_batch_videos": "按表批量出视频；须用户明确确认生成",
    "multi_angle": "换机位再画；须已有图；params: azimuth(0正/90右/180背/270左)、elevation、shot=close|medium|wide、extraPrompt",
    "lighting": "改主光不换主体；须已有图；params: direction=left|top|right|front|bottom|back、brightness、rimLight、smartMode、extraPrompt",
    "cinematic_lighting": "宫格式电影光影校正；前置 image_input；可选 userPrompt",
    "panorama": "全景图；前置 image_input；可选 userPrompt",
    "panorama_720": "720 环绕全景；前置 image_input；可选 userPrompt",
    "grid_9": "3×3 多格变体；前置 image_input；可选 userPrompt",
    "grid_25": "25 宫格连贯分镜；前置 image_input；可选 userPrompt",
    "plot_grid_4": "起承转合四格；建议 userPrompt 写四格内容",
    "frame_forward_3s": "推演约 3 秒后；可选 userPrompt",
    "frame_back_5s": "推演约 5 秒前；可选 userPrompt",
    "multi_cam_grid_9": "同场多机位九宫格；可选 userPrompt",
    "face_tri_view": "脸部正侧 3/4 三视图；可选 userPrompt",
    "character_sheet": "角色设定图；可无参考文生；可选 userPrompt",
    "character_tri_view": "角色全身三视图；可无参考文生；可选 userPrompt",
    "scene_sheet": "场景设定图；可无参考文生；可选 userPrompt",
    "product_sheet": "产品多视角设定图；可无参考文生；电影级宣传片不要用这个锁身份",
    "outpaint": "扩画布；须已有图",
    "cutout": "抠图去背景；须已有图",
    "hd_upscale": "图片超分；须已有图；可选 hdScale（默认 2）",
    "portrait_adjust": "人像质感精修；须已有图；params.userPrompt 必填",
    "emotion_adjust": "改表情；须已有图；params.userPrompt 必填",
    "drawing_board_ai": "对图片按提示词再画一版；params.userPrompt 必填",
    "visual_style": "套画风；前置 image_input；userPrompt 或节点已有风格",
    "hd_upscale_video": "视频超分；前置有媒体的 video_input",
    "video_smart_matting": "视频抠像；前置有媒体的 video_input；源片宜 ≤12 秒",
    "video_subject_remove": "去掉画面主体；params.userPrompt 说明去掉什么；源片 ≤12 秒，超长会自动截取",
    "video_subject_edit": "改视频里的人/物外观或画风；params.userPrompt 必填（如改成动漫角色）；源片 ≤12 秒，超长会自动截取前 12 秒",
    "video_subject_replace": "把视频主体换成参考图里的主体；params.userPrompt + 参考图（先 connect 到视频 ref_in 或 params.refImageNodeId）；源片 ≤12 秒",
    "video_subtitle_smart_erase": "智能去字幕；前置有媒体的 video_input",
    "video_subtitle_box_erase": "框选去字幕，须用户在节点上手动划框；助手请改用 video_subtitle_smart_erase",
    "vocal_separate": "人声/伴奏分开；前置 audio_input 或视频音轨",
    "vocal_remove": "去人声留伴奏；前置 audio_input 或视频音轨",
}

_CANVAS_TOOLS_GUIDE_PATH = Path(__file__).resolve().parent / "agent_canvas_tools.md"


def load_canvas_tools_guide() -> str:
    """读取画布工具说明书（是什么 / 何时用 / params / 用完做什么）。"""
    try:
        return _CANVAS_TOOLS_GUIDE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def format_canvas_tools_index(primary_by_tool: dict[str, Any] | None = None) -> str:
    """分组速查：官方名、功能要点、主模型。说明书附录或加载失败兜底。"""
    from .agent_tool_catalog import format_canvas_tools_detailed_index

    return format_canvas_tools_detailed_index(primary_by_tool)


SERVER_TOOL_NAMES = frozenset(
    {
        "get_canvas_state",
        "inspect_node",
        "list_models",
        "list_canvas_tools",
        "list_skills",
        "load_skill",
        "load_skill_file",
        "update_plan",
    }
)
TERMINAL_TOOL_NAMES = frozenset({"ask_user"})
CLIENT_TOOL_NAMES = frozenset(
    {
        "add_node",
        "update_node_params",
        "connect_nodes",
        "disconnect_nodes",
        "layout_hint",
        "run_canvas_tool",
        "generate_node",
    }
)

_ADD_OP = {
    "text_input": "add_text_node",
    "image_input": "add_image_node",
    "video_input": "add_video_node",
    "audio_input": "add_audio_node",
    "document_input": "add_document_node",
    "storyboard_grid": "add_storyboard_node",
    "director_stage": "add_director_node",
}


def _fn(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": schema},
    }


def openai_tool_schemas() -> list[dict[str, Any]]:
    """注入 Chat Completions 的 tools 列表。"""
    run_enum = list(CANVAS_RUN_TOOL_IDS)
    return [
        _fn(
            "get_canvas_state",
            "读取本轮画布目录（节点/边/媒体/失败，不含非焦点正文）。每轮思考开始时必须已读。",
            {},
        ),
        _fn(
            "inspect_node",
            "查看一个或多个节点的完整参数与文案。优先一次传入 nodeIds（最多 20）减少来回；优先返回本轮画布快照里的未保存正文；OSS/工作流可能滞后。改非焦点节点文案前必须先 inspect。",
            {
                "nodeId": {"type": "string", "description": "单个节点 id"},
                "nodeIds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "批量节点 id，最多 20 个",
                },
                "nodeName": {"type": "string"},
            },
        ),
        _fn(
            "list_models",
            "列出可用模型及输入上限。本轮已注入画布已有/首选模型能力；换未列出的模型或仍不确定上限时必须调用。传入 modelId 查看单个详情。",
            {
                "category": {
                    "type": "string",
                    "enum": ["text", "image", "video", "audio"],
                },
                "modelId": {
                    "type": "string",
                    "description": "查该模型的输入类型与上限；不传则列出全部",
                },
            },
        ),
        _fn(
            "list_canvas_tools",
            "返回分组工具目录（官方名/id/功能要点/主模型）与完整说明书。本轮已注入【画布工具速查】；需要 params 细节或仍不确定时再调。禁止近义猜测 tool id。",
            {},
        ),
        _fn(
            "list_skills",
            "列出可用技能包（name + description）。本轮 system 已注入【技能目录】；不确定何时用某技能时再调。",
            {},
        ),
        _fn(
            "load_skill",
            "按需加载一份技能的 SKILL.md 正文（不含 references 整包）。用户点名芯片/$name/匹配 description 时必须调用（本轮已绑定并预载过则可跳过）。name 或 slug 二选一。",
            {
                "name": {
                    "type": "string",
                    "description": "Agent Skills name，如 product-cinematic-commercial",
                },
                "slug": {
                    "type": "string",
                    "description": "本系统 slug，如 product_cinematic_commercial",
                },
            },
        ),
        _fn(
            "load_skill_file",
            "按需读取技能包内单个 markdown（如 references/generation.md）。path 须为包内相对路径。",
            {
                "name": {"type": "string", "description": "技能 name（与 load_skill 相同）"},
                "slug": {"type": "string", "description": "技能 slug"},
                "path": {
                    "type": "string",
                    "description": "包内相对路径，如 references/node-layout.md",
                },
            },
            ["path"],
        ),
        _fn(
            "update_plan",
            "更新本会话可见进度计划（pending/in_progress/completed）。同一时刻仅一步 in_progress。"
            "只用于进度展示，禁止用 plan 当状态机驱动工具顺序。长配方（宣传片/出海/爆款）建议维护。",
            {
                "steps": {
                    "type": "array",
                    "description": "步骤列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string", "description": "步骤标题"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                            },
                        },
                    },
                },
                "explanation": {
                    "type": "string",
                    "description": "可选短说明",
                },
            },
            ["steps"],
        ),
        _fn(
            "add_node",
            "在画布新建节点。用户准星点名了节点时不要新建，对已有节点动手；没有点名才新建。能复用现有节点时不要新建。",
            {
                "type": {
                    "type": "string",
                    "enum": [
                        "text_input",
                        "image_input",
                        "video_input",
                        "audio_input",
                        "document_input",
                        "storyboard_grid",
                        "director_stage",
                    ],
                },
                "label": {"type": "string"},
                "content": {"type": "string", "description": "文本节点正文"},
                "prompt": {"type": "string"},
                "model": {"type": "string"},
                "assetId": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "generationOptions": {"type": "object"},
            },
            ["type"],
        ),
        _fn(
            "update_node_params",
            "增量修改已有节点：params 只传要改的字段（对齐 apply_patch）。"
            "改一句 prompt 时 params={prompt:...} 即可；禁止重发整份 generationOptions。"
            "generationOptions 也只传要改的子键（如 duration/aspectRatio）。可先 inspect_node。",
            {
                "nodeId": {"type": "string"},
                "nodeName": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": (
                        "仅含变更字段：prompt/content/model/assetId/label/"
                        "generationOptions(子键补丁) 等。不要塞未改字段。"
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": "可选；等价于 params.prompt（只改提示词时可用）",
                },
                "content": {"type": "string"},
                "model": {"type": "string"},
                "generationOptions": {
                    "type": "object",
                    "description": "可选；只含要改的子键，勿整表复制",
                },
            },
            ["nodeId"],
        ),
        _fn(
            "connect_nodes",
            "把上游参考接到下游（默认 targetHandle=ref_in）。连参考前须已对照【本轮模型能力】或 list_models 的输入上限，超上限不要硬连。两端须已存在或为本轮 add_node 的临时节点。",
            {
                "source": {"type": "string"},
                "sourceName": {"type": "string"},
                "target": {"type": "string"},
                "targetName": {"type": "string"},
                "sourceHandle": {"type": "string"},
                "targetHandle": {"type": "string"},
            },
            ["source", "target"],
        ),
        _fn(
            "disconnect_nodes",
            "去掉两点之间的参考连线。",
            {"source": {"type": "string"}, "target": {"type": "string"}},
            ["source", "target"],
        ),
        _fn(
            "layout_hint",
            "只移动节点位置，避免重叠。",
            {
                "nodeId": {"type": "string"},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            ["nodeId", "x", "y"],
        ),
        _fn(
            "run_canvas_tool",
            "在指定节点上执行画布工具。常见：multi_angle（azimuth 0正/90右/180背/270左、elevation、shot=close|medium|wide）；lighting（direction、rimLight、brightness）；grid_9/grid_25/plot_grid_4 等宫格（可选 userPrompt，完了可 grid_split）；storyboard/blocking_storyboard（params.userPrompt 必填，是一张合成分镜图不是表格）；video_subject_edit（params.userPrompt 必填，把片里的人/物改成什么样；主模型见速查表）；video_subject_replace（userPrompt + 参考图连到视频 ref_in 或 params.refImageNodeId）。源片须 ≤12 秒，超长会自动截取。完整说明先 list_canvas_tools。tool 必须是目录内 id。",
            {
                "tool": {"type": "string", "enum": run_enum},
                "nodeId": {"type": "string"},
                "nodeName": {"type": "string"},
                "params": {
                    "type": "object",
                    "description": "工具特定参数。故事板必填 userPrompt；主体修改/替换/消除必填 userPrompt；主体替换再加 refImageNodeId 或先把参考图 connect 到视频 ref_in；多角度 azimuth/elevation/shot；打光 direction/rimLight；出海 targetMarketId；宫格/设定图可选 userPrompt。",
                },
            },
            ["tool", "nodeId"],
        ),
        _fn(
            "generate_node",
            "对可生成的已有节点提交生成（走报价与算力预扣）。用户点名了节点就对该 nodeId 生成，不要先 add_node。点名的是图片且要出视频：对已连接到该图的 video_input 生成；没有才允许先加一个视频节点。视频出片须用户本轮已明确要求。本轮一旦提交成功，不要再 generate_node。",
            {
                "nodeId": {"type": "string"},
                "nodeName": {"type": "string"},
            },
            ["nodeId"],
        ),
        _fn(
            "ask_user",
            "向用户确认缺的信息或高算力出视频。给出可选按钮。",
            {
                "prompt": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "label": {"type": "string"},
                        },
                    },
                },
            },
            ["prompt"],
        ),
    ]


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def tool_call_name_args(tc: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """解析上游 tool_call → (id, name, args)。"""
    tid = str(tc.get("id") or "").strip()
    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
    name = str(fn.get("name") or tc.get("name") or "").strip()
    args = parse_tool_arguments(fn.get("arguments") if fn else tc.get("arguments"))
    return tid, name, args


async def execute_server_tool(
    name: str,
    args: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None,
    project_id: str | None = None,
    db: AsyncSession | None = None,
    skill_slug: str | None = None,
    skill_row: Any | None = None,
    session: Any | None = None,
) -> str:
    """执行只读/会话侧工具，返回给模型的文本。"""
    if name == "update_plan":
        from sqlalchemy.orm.attributes import flag_modified

        from .agent_update_plan import apply_update_plan_args, format_update_plan_result

        plan = apply_update_plan_args(args if isinstance(args, dict) else {})
        if not plan:
            return json.dumps(
                {"error": "update_plan 需要非空 steps（step + status）"},
                ensure_ascii=False,
            )
        if session is not None:
            brief = (
                dict(session.brief_json)
                if isinstance(getattr(session, "brief_json", None), dict)
                else {}
            )
            brief["runtimePlan"] = plan
            session.brief_json = brief
            try:
                flag_modified(session, "brief_json")
            except Exception:  # noqa: BLE001
                pass
            if db is not None:
                await db.flush()
        return format_update_plan_result(plan)
    if name == "get_canvas_state":
        from .agent_followup import _snapshot_summary

        return _snapshot_summary(snapshot)
    if name == "inspect_node":
        ids = collect_inspect_node_ids(args)
        if not ids:
            return await _inspect_node_text(
                "",
                snapshot,
                node_name=str(args.get("nodeName") or ""),
                project_id=project_id,
                db=db,
            )
        if len(ids) == 1:
            return await _inspect_node_text(
                ids[0],
                snapshot,
                node_name=str(args.get("nodeName") or ""),
                project_id=project_id,
                db=db,
            )
        parts = await asyncio.gather(
            *[
                _inspect_node_text(
                    nid,
                    snapshot,
                    project_id=project_id,
                    db=db,
                )
                for nid in ids
            ]
        )
        chunks: list[str] = []
        for nid, text in zip(ids, parts):
            chunks.append(f"===== inspect {nid} =====\n{text}")
        return "\n\n".join(chunks)
    if name == "list_models":
        from .agent_model_caps import list_models_for_agent

        return list_models_for_agent(
            category=str(args.get("category") or ""),
            model_id=str(args.get("modelId") or args.get("model") or ""),
        )
    if name == "list_canvas_tools":
        from .agent_canvas_tool_models import (
            format_agent_canvas_tool_models_doc,
            load_agent_tool_primary_models,
        )
        from .agent_canvas_tool_names import format_official_tool_name_table

        primary: dict[str, str] | None = None
        if db is not None:
            try:
                primary = await load_agent_tool_primary_models(db)
            except Exception:  # noqa: BLE001
                primary = None
        guide = load_canvas_tools_guide()
        index = format_canvas_tools_index(primary)
        names = format_official_tool_name_table()
        models_doc = format_agent_canvas_tool_models_doc(primary)
        parts = [
            "## 工具目录（官方名 / id / 特点 / 主模型）\n" + index,
            models_doc,
            names,
        ]
        if guide:
            parts.append(guide)
        return "\n\n---\n".join(parts)
    if name in ("list_skills", "load_skill", "load_skill_file"):
        from .skill_docs import (
            format_skill_catalog_index,
            load_skill_body_text,
            load_skill_package_file_text,
        )

        bound = skill_row
        if name == "list_skills":
            return await format_skill_catalog_index(db, bound_skill=bound)
        key = str(args.get("name") or args.get("slug") or skill_slug or "").strip()
        if name == "load_skill":
            return await load_skill_body_text(db, key, skill_row=bound)
        path = str(args.get("path") or "").strip()
        return await load_skill_package_file_text(
            db, key or str(skill_slug or ""), path, skill_row=bound
        )
    return json.dumps({"error": f"未知服务端工具 {name}"}, ensure_ascii=False)


_INSPECT_TEXT_MAX = 12000
_INSPECT_BATCH_MAX = 20


def collect_inspect_node_ids(args: dict[str, Any]) -> list[str]:
    """从 nodeId / nodeIds 收集待查看节点，去重后最多 20 个。"""
    ids: list[str] = []
    raw_ids = args.get("nodeIds")
    if isinstance(raw_ids, list):
        for item in raw_ids:
            s = str(item or "").strip()
            if s:
                ids.append(s)
    elif isinstance(raw_ids, str) and raw_ids.strip():
        ids.append(raw_ids.strip())
    nid = str(args.get("nodeId") or "").strip()
    if nid:
        ids.append(nid)
    seen: set[str] = set()
    uniq: list[str] = []
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        uniq.append(item)
        if len(uniq) >= _INSPECT_BATCH_MAX:
            break
    return uniq


def _find_snapshot_node(
    snapshot: dict[str, Any] | None,
    node_id: str,
    node_name: str,
) -> dict[str, Any] | None:
    nodes = snapshot.get("nodes") if isinstance(snapshot, dict) else None
    if not isinstance(nodes, list):
        return None
    nid = (node_id or "").strip()
    nname = (node_name or "").strip()
    if nid:
        for n in nodes:
            if isinstance(n, dict) and str(n.get("id") or "").strip() == nid:
                return n
    if nname:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            label = str(n.get("label") or n.get("name") or "").strip()
            if label == nname:
                return n
    return None


async def _load_node_oss_text(db: AsyncSession, project_id: str, node_id: str) -> str:
    """从 OSS 读取文本节点全文（与 GET /node-texts 同源）。"""
    from ..integrations.oss.canvas_storage import get_canvas_storage
    from ..models.project import Project
    from .project_scope import project_storage_folder

    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError):
        return ""
    project = (await db.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
    if project is None:
        return ""
    try:
        folder = project_storage_folder(project)
    except ValueError:
        return ""
    storage = get_canvas_storage()
    try:
        meta = await asyncio.to_thread(
            storage.read_json,
            str(project.id),
            f"text/{node_id}.meta.json",
            storage_folder=folder,
        )
    except Exception:
        return ""
    if not isinstance(meta, dict):
        return ""
    oss_key = meta.get("ossKey") or storage.project_key(
        str(project.id), f"text/{node_id}.txt", storage_folder=folder
    )
    try:
        content_bytes = await asyncio.to_thread(storage.get_bytes, str(oss_key or ""))
    except Exception:
        content_bytes = b""
    content = content_bytes.decode("utf-8") if content_bytes else str(meta.get("content") or "")
    return content.strip()


_WF_SNAP_NODE_MAX = 800
_WF_SNAP_EDGE_MAX = 1200
_LIVE_TEXT_MAX = 2000


def empty_canvas_snapshot(*, source: str = "empty") -> dict[str, Any]:
    """无工作流时的空目录，允许思考（OpenAPI 新建项目）。"""
    return {
        "nodes": [],
        "edges": [],
        "nodeCount": 0,
        "edgeCount": 0,
        "source": source,
    }


def client_snapshot_usable(snapshot: dict[str, Any] | None) -> bool:
    """前端快照须带 nodes 或 nodeCount，避免空对象挡住工作流合成。"""
    if not isinstance(snapshot, dict):
        return False
    if isinstance(snapshot.get("nodes"), list):
        return True
    return snapshot.get("nodeCount") is not None


def _clip_live_text(raw: Any, limit: int = _LIVE_TEXT_MAX) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)] + "…"


def _compact_generation_options(raw: Any) -> dict[str, str]:
    """与前端 compactGenerationOptions 对齐：只留短字符串选项。"""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, val in list(raw.items())[:16]:
        if val is None:
            continue
        k = str(key or "").strip()
        if not k:
            continue
        out[k] = str(val).strip()
    return out


def _pick_live_fields(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    prompt = _clip_live_text(item.get("prompt"))
    content = _clip_live_text(item.get("content"))
    if prompt:
        out["prompt"] = prompt
    if content:
        out["content"] = content
    if item.get("shotCount") is not None:
        out["shotCount"] = item.get("shotCount")
    if item.get("shotsPreview"):
        out["shotsPreview"] = item.get("shotsPreview")
    go = _compact_generation_options(item.get("generationOptions"))
    if go:
        out["generationOptions"] = go
    return out


async def _load_latest_flow_dict(
    db: AsyncSession, project_id: str
) -> dict[str, Any] | None:
    """读取项目最新工作流 flow_json（OSS 指针已展开）。"""
    from ..integrations.oss.canvas_storage import get_canvas_storage
    from ..models.project import Project, Workflow
    from .project_scope import project_storage_folder
    from .workflow_recovery import resolve_workflow_flow_json

    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError):
        return None
    project = (await db.execute(select(Project).where(Project.id == pid))).scalar_one_or_none()
    if project is None:
        return None
    wf = (
        await db.execute(
            select(Workflow)
            .where(Workflow.project_id == pid)
            .order_by(Workflow.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if wf is None:
        return None
    try:
        folder = project_storage_folder(project)
    except ValueError:
        folder = str(project.storage_folder or "")
    try:
        flow, _key = await asyncio.to_thread(
            resolve_workflow_flow_json,
            str(wf.flow_json or "{}"),
            project_id=str(project.id),
            storage_folder=folder,
            storage=get_canvas_storage(),
        )
    except Exception:
        return None
    try:
        data = json.loads(flow or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def snapshot_from_flow_dict(flow: dict[str, Any]) -> dict[str, Any]:
    """用已保存工作流合成目录快照（可能落后于画布未保存改动）。"""
    nodes_raw = flow.get("nodes") if isinstance(flow.get("nodes"), list) else []
    edges_raw = flow.get("edges") if isinstance(flow.get("edges"), list) else []
    mapped: list[dict[str, Any]] = []
    live_params: dict[str, Any] = {}
    listed = [n for n in nodes_raw if isinstance(n, dict)][:_WF_SNAP_NODE_MAX]
    for n in listed:
        nid = str(n.get("id") or "").strip()
        if not nid:
            continue
        inner = n.get("data") if isinstance(n.get("data"), dict) else {}
        params = inner.get("params") if isinstance(inner.get("params"), dict) else {}
        pos = n.get("position") if isinstance(n.get("position"), dict) else {}
        asset_id = str(params.get("assetId") or "").strip()
        model = str(params.get("model") or "").strip()
        status = str(inner.get("status") or "").strip()
        ntype = str(n.get("type") or "")
        label = str(inner.get("label") or "")
        has_media = bool(
            params.get("imageUrl")
            or params.get("videoUrl")
            or params.get("audioUrl")
            or asset_id
        )
        item: dict[str, Any] = {
            "id": nid,
            "type": ntype,
            "label": label,
            "hasMedia": has_media,
        }
        try:
            item["x"] = int(round(float(pos.get("x"))))
        except (TypeError, ValueError):
            pass
        try:
            item["y"] = int(round(float(pos.get("y"))))
        except (TypeError, ValueError):
            pass
        if asset_id:
            item["assetId"] = asset_id
        if model:
            item["model"] = model
        if status:
            item["status"] = status
        mapped.append(item)
        live = _pick_live_fields(
            {
                "prompt": params.get("prompt"),
                "content": params.get("content"),
                "generationOptions": params.get("generationOptions"),
            }
        )
        shots = params.get("shots")
        if isinstance(shots, list) and shots:
            live["shotCount"] = len(shots)
        if live:
            live_params[nid] = live
    edges: list[dict[str, Any]] = []
    for e in edges_raw:
        if not isinstance(e, dict):
            continue
        src = str(e.get("source") or "").strip()
        tgt = str(e.get("target") or "").strip()
        if not src or not tgt:
            continue
        edges.append(
            {
                "source": src,
                "target": tgt,
                "sourceHandle": e.get("sourceHandle"),
                "targetHandle": e.get("targetHandle"),
            }
        )
        if len(edges) >= _WF_SNAP_EDGE_MAX:
            break
    truncated = len(listed) < len(nodes_raw) or len(edges) < len(
        [x for x in edges_raw if isinstance(x, dict)]
    )
    out: dict[str, Any] = {
        "nodeCount": len(nodes_raw),
        "edgeCount": len(edges_raw),
        "nodes": mapped,
        "edges": edges,
        "source": "workflow",
    }
    if truncated:
        out["truncated"] = True
    if live_params:
        out["liveParams"] = live_params
    return out


async def snapshot_from_latest_workflow(
    db: AsyncSession, project_id: str | int | None
) -> dict[str, Any]:
    """OpenAPI / 无前端快照：用最新工作流合成目录；没有工作流则空画布。"""
    if project_id is None:
        return empty_canvas_snapshot()
    flow = await _load_latest_flow_dict(db, str(project_id))
    if not flow:
        return empty_canvas_snapshot()
    return snapshot_from_flow_dict(flow)


async def _load_graph_node_params(
    db: AsyncSession, project_id: str, node_id: str
) -> dict[str, Any] | None:
    """从最新工作流 flow_json 取节点 params（画布 React Flow，不是 Project Graph）。"""
    data = await _load_latest_flow_dict(db, project_id)
    if not isinstance(data, dict):
        return None
    nodes = data.get("nodes") if isinstance(data.get("nodes"), list) else None
    if not isinstance(nodes, list):
        return None
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if str(n.get("id") or "").strip() != node_id:
            continue
        inner = n.get("data") if isinstance(n.get("data"), dict) else {}
        params = inner.get("params") if isinstance(inner.get("params"), dict) else {}
        return dict(params)
    return None


def _focused_fields_for_node(snapshot: dict[str, Any] | None, node_id: str) -> dict[str, Any]:
    """快照焦点短正文。"""
    items = snapshot.get("focusedContent") if isinstance(snapshot, dict) else None
    if not isinstance(items, list):
        return {}
    for it in items:
        if isinstance(it, dict) and str(it.get("id") or "").strip() == node_id:
            return _pick_live_fields(it)
    return {}


def _live_fields_for_node(snapshot: dict[str, Any] | None, node_id: str) -> dict[str, Any]:
    """本轮画布现场正文：liveParams（含非焦点未保存）优先，其次焦点块。"""
    live_map = snapshot.get("liveParams") if isinstance(snapshot, dict) else None
    if isinstance(live_map, dict):
        item = live_map.get(node_id)
        if isinstance(item, dict):
            picked = _pick_live_fields(item)
            if picked:
                return picked
    return _focused_fields_for_node(snapshot, node_id)


async def _inspect_node_text(
    node_id: str,
    snapshot: dict[str, Any] | None,
    *,
    node_name: str = "",
    project_id: str | None = None,
    db: AsyncSession | None = None,
) -> str:
    found = _find_snapshot_node(snapshot, node_id, node_name)
    nid = str((found or {}).get("id") or node_id or "").strip()
    if not nid:
        return "缺少 nodeId"
    oss_text = ""
    graph_params: dict[str, Any] | None = None
    if db is not None and project_id:
        oss_text = await _load_node_oss_text(db, project_id, nid)
        graph_params = await _load_graph_node_params(db, project_id, nid)
    # 目录截断时快照可能没有该节点；工作流或 liveParams 里仍可能有全文
    live_early = _live_fields_for_node(snapshot, nid)
    if found is None and graph_params is None and not oss_text and not live_early:
        return f"快照和工作流中都没有节点 {nid}。请用目录里的真实 id。"
    if found is None:
        found = {"id": nid, "from": "live_snapshot" if live_early else "workflow"}
    edges = snapshot.get("edges") if isinstance(snapshot, dict) else []
    ups: list[str] = []
    downs: list[str] = []
    if isinstance(edges, list):
        for e in edges:
            if not isinstance(e, dict):
                continue
            if str(e.get("target") or "") == nid:
                ups.append(str(e.get("source") or ""))
            if str(e.get("source") or "") == nid:
                downs.append(str(e.get("target") or ""))
    if len(oss_text) > _INSPECT_TEXT_MAX:
        oss_text = oss_text[: _INSPECT_TEXT_MAX] + "…(已截断)"
    payload: dict[str, Any] = {
        "node": found,
        "upstream": ups,
        "downstream": downs,
    }
    live = _live_fields_for_node(snapshot, nid)
    if live:
        payload["live"] = live
    if oss_text:
        payload["fullText"] = oss_text
    merged = dict(graph_params) if graph_params else {}
    # 未自动保存的 prompt/content/generationOptions 以本轮快照为准
    if live.get("prompt"):
        merged["prompt"] = live["prompt"]
        merged["_promptSource"] = "live_snapshot"
    if live.get("content"):
        merged["content"] = live["content"]
        merged["_contentSource"] = "live_snapshot"
    if isinstance(live.get("generationOptions"), dict) and live["generationOptions"]:
        merged["generationOptions"] = live["generationOptions"]
        merged["_generationOptionsSource"] = "live_snapshot"
    if live.get("shotCount") is not None and "shotCount" not in merged:
        merged["shotCount"] = live.get("shotCount")
    if merged:
        raw_params = json.dumps(merged, ensure_ascii=False)
        if len(raw_params) > 12000:
            shots = merged.get("shots")
            payload["params"] = {
                "_truncated": True,
                "keys": list(merged.keys())[:40],
                "shotCount": len(shots) if isinstance(shots, list) else 0,
                "shotsPreview": shots[:8] if isinstance(shots, list) else [],
                "prompt": merged.get("prompt"),
                "content": merged.get("content"),
                "generationOptions": merged.get("generationOptions"),
            }
        else:
            payload["params"] = merged
    payload["note"] = (
        "live / params 的 prompt、content、generationOptions 优先来自本轮画布快照"
        "（含未自动保存的改动）。fullText 来自 OSS，可能滞后。"
    )
    return json.dumps(payload, ensure_ascii=False)


_NODE_OR_ATTACH_RE = re.compile(r"\[(?:节点|附件):[^\]]*\]")
# 仅工具名、不能当修改说明
_BARE_TOOL_PROMPTS = frozenset(
    {"主体修改", "主体替换", "主体消除", "修改主体", "替换主体", "消除主体"}
)
# 缺 params.userPrompt 时用本轮用户原话补上
_PROMPT_INJECT_TOOLS = frozenset(
    {
        "video_subject_edit",
        "video_subject_replace",
        "video_subject_remove",
        "storyboard",
        "blocking_storyboard",
        "portrait_adjust",
        "emotion_adjust",
        "drawing_board_ai",
    }
)


def intent_prompt_from_user_text(user_text: str) -> str:
    """去掉准星/附件标记，得到可当提示词的用户原话。"""
    text = _NODE_OR_ATTACH_RE.sub(" ", user_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:800]


def _inject_user_prompt(params: dict[str, Any], user_text: str) -> dict[str, Any]:
    """画面编辑等工具若没写 userPrompt，用本轮用户原话补上，避免助手空跑失败。"""
    existing = str(params.get("userPrompt") or params.get("prompt") or "").strip()
    if existing and existing not in _BARE_TOOL_PROMPTS:
        return params
    fallback = intent_prompt_from_user_text(user_text)
    if not fallback or fallback in _BARE_TOOL_PROMPTS:
        return params
    out = dict(params)
    out["userPrompt"] = fallback
    return out


def client_tool_to_canvas_ops(
    tool_call_id: str,
    name: str,
    args: dict[str, Any],
    *,
    user_text: str = "",
) -> list[dict[str, Any]]:
    """把客户端工具转成前端可投影的 canvasOps。"""
    tid = (tool_call_id or "").strip()
    if name == "add_node":
        ntype = str(args.get("type") or "image_input").strip()
        op = _ADD_OP.get(ntype, "add_image_node")
        params: dict[str, Any] = {}
        if args.get("prompt") is not None:
            params["prompt"] = args.get("prompt")
        if args.get("content") is not None:
            params["content"] = args.get("content")
        if args.get("model"):
            params["model"] = args.get("model")
        if args.get("assetId"):
            params["assetId"] = args.get("assetId")
        if isinstance(args.get("generationOptions"), dict):
            params["generationOptions"] = args.get("generationOptions")
        extra = args.get("params")
        if isinstance(extra, dict):
            params.update(extra)
        temp_id = str(args.get("tempId") or "").strip() or f"tmp_{tid[-10:] or tid}"
        item: dict[str, Any] = {
            "op": op,
            "toolCallId": tid,
            "tempId": temp_id,
            "label": str(args.get("label") or "").strip() or None,
            "content": args.get("content") if ntype == "text_input" else None,
            "prompt": args.get("prompt"),
            "x": args.get("x"),
            "y": args.get("y"),
            "params": params or None,
        }
        return [{k: v for k, v in item.items() if v is not None}]
    if name == "update_node_params":
        from .agent_params_diff import slim_update_node_params_args

        params = slim_update_node_params_args(args if isinstance(args, dict) else {})
        if not params:
            # 无可投影变更：不写 canvasOps（避免空更新）
            return []
        return [
            {
                "op": "update_node_params",
                "toolCallId": tid,
                "nodeId": str(args.get("nodeId") or "").strip(),
                "nodeName": str(args.get("nodeName") or "").strip() or None,
                "params": params,
            }
        ]
    if name == "connect_nodes":
        return [
            {
                "op": "connect_nodes",
                "toolCallId": tid,
                "source": str(args.get("source") or "").strip(),
                "sourceName": str(args.get("sourceName") or "").strip() or None,
                "target": str(args.get("target") or "").strip(),
                "targetName": str(args.get("targetName") or "").strip() or None,
                "sourceHandle": args.get("sourceHandle"),
                "targetHandle": args.get("targetHandle") or "ref_in",
            }
        ]
    if name == "disconnect_nodes":
        return [
            {
                "op": "disconnect_nodes",
                "toolCallId": tid,
                "source": str(args.get("source") or "").strip(),
                "target": str(args.get("target") or "").strip(),
            }
        ]
    if name == "layout_hint":
        return [
            {
                "op": "layout_hint",
                "toolCallId": tid,
                "nodeId": str(args.get("nodeId") or "").strip(),
                "x": args.get("x"),
                "y": args.get("y"),
            }
        ]
    if name == "run_canvas_tool":
        tool = str(args.get("tool") or "").strip()
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        params = dict(params)
        if tool in _PROMPT_INJECT_TOOLS:
            params = _inject_user_prompt(params, user_text)
        return [
            {
                "op": "run_canvas_tool",
                "toolCallId": tid,
                "tool": tool,
                "nodeId": str(args.get("nodeId") or "").strip(),
                "nodeName": str(args.get("nodeName") or "").strip() or None,
                "params": params or None,
            }
        ]
    if name == "generate_node":
        return [
            {
                "op": "generate_node",
                "toolCallId": tid,
                "nodeId": str(args.get("nodeId") or "").strip(),
                "nodeName": str(args.get("nodeName") or "").strip() or None,
            }
        ]
    return []
