"""
React Flow workflow JSON ↔ ComfyUI prompt JSON bidirectional serializer.

ComfyUI workflow format:
{
  "node_id": {
    "inputs": { "param": value_or_link },
    "class_type": "KSampler",
    "_meta": { "title": "..." }
  }
}

React Flow format (stored as nodes/edges):
{
  "nodes": [{ id, type, position, data: { label, params, inputs, outputs } }],
  "edges": [{ id, source, target, sourceHandle, targetHandle }]
}
"""

from typing import Any

# Node type mapping: React Flow type → ComfyUI class_type
FLOW_TO_COMFY: dict[str, str] = {
    "model_loader": "CheckpointLoaderSimple",
    "prompt": "CLIPTextEncode",
    "ksampler": "KSampler",
    "vae_decode": "VAEDecode",
    "image_input": "LoadImage",
    "image_preview": "PreviewImage",
    "lora": "LoraLoader",
    "upscale": "UpscaleImage",
}

# Inverse mapping
COMFY_TO_FLOW: dict[str, str] = {v: k for k, v in FLOW_TO_COMFY.items()}

# ComfyUI input key mapping per node type
INPUT_MAP: dict[str, dict[str, str]] = {
    "CheckpointLoaderSimple": {"checkpoint": "ckpt_name"},
    "CLIPTextEncode": {"positive": "text", "negative": "text"},
    "KSampler": {
        "model": "model",
        "positive": "positive",
        "negative": "negative",
        "latent_image": "latent_image",
        "seed": "seed",
        "steps": "steps",
        "cfg": "cfg",
        "sampler_name": "sampler",
        "scheduler": "scheduler",
        "width": "width",
        "height": "height",
    },
    "VAEDecode": {"latent": "samples", "vae": "vae"},
    "LoraLoader": {"model": "model", "lora_name": "lora_name", "strength": "strength_model"},
    "LoadImage": {"imageUrl": "image"},
    "UpscaleImage": {"image": "image", "scale": "upscale_factor", "method": "model_name"},
    "LLMText": {"model": "model", "text": "prompt", "max_tokens": "max_tokens", "temperature": "temperature"},
}

def flow_to_comfy(nodes: list[dict], edges: list[dict]) -> dict[str, dict]:
    """Convert React Flow nodes/edges to ComfyUI prompt JSON."""
    prompt: dict[str, dict] = {}

    # First pass: create node entries
    for node in nodes:
        node_id = str(node["id"])
        flow_type = str(node.get("type", ""))
        class_type = FLOW_TO_COMFY.get(flow_type, flow_type)

        prompt[node_id] = {
            "class_type": class_type,
            "inputs": {},
        }

        # Map params to ComfyUI inputs
        params = node.get("data", {}).get("params", {})
        mapping = INPUT_MAP.get(class_type, {})
        for flow_key, comfy_key in mapping.items():
            if flow_key in params:
                prompt[node_id]["inputs"][comfy_key] = params[flow_key]

    # Second pass: wire edges
    for edge in edges:
        source_id = str(edge["source"])
        target_id = str(edge["target"])
        source_handle = edge.get("sourceHandle", "")
        target_handle = edge.get("targetHandle", "")

        if target_id not in prompt:
            continue

        # Determine which ComfyUI input key maps to this target handle
        target_class = prompt[target_id]["class_type"]
        input_map = INPUT_MAP.get(target_class, {})

        # The target_handle is our flow port ID (e.g., "model", "positive")
        # Map it to the ComfyUI input key
        comfy_input_key = input_map.get(target_handle, target_handle)

        # ComfyUI link format: [source_node_id, source_output_index]
        # We default to output index 0
        prompt[target_id]["inputs"][comfy_input_key] = [source_id, 0]

    # Ensure required inputs have defaults
    for node_id, node_data in prompt.items():
        class_type = node_data["class_type"]

        # KSampler defaults
        if class_type == "KSampler":
            node_data["inputs"].setdefault("seed", 0)
            node_data["inputs"].setdefault("steps", 20)
            node_data["inputs"].setdefault("cfg", 7.0)
            node_data["inputs"].setdefault("sampler_name", "euler")
            node_data["inputs"].setdefault("scheduler", "normal")
            node_data["inputs"].setdefault("denoise", 1.0)

        # Empty latent image default
        if class_type == "KSampler" and "latent_image" not in node_data["inputs"]:
            node_data["inputs"]["latent_image"] = ["0", 0]  # placeholder

    return prompt


def comfy_to_flow(prompt: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Convert ComfyUI prompt JSON back to React Flow nodes/edges."""
    nodes: list[dict] = []
    edges: list[dict] = []

    for node_id, node_data in prompt.items():
        class_type = node_data["class_type"]

        # Skip internal comfy nodes
        if class_type.startswith("_"):
            continue

        flow_type = COMFY_TO_FLOW.get(class_type, class_type.lower())
        params: dict[str, Any] = {}

        mapping = INPUT_MAP.get(class_type, {})
        reverse_map = {v: k for k, v in mapping.items()}

        for comfy_key, value in node_data.get("inputs", {}).items():
            if isinstance(value, list) and len(value) == 2:
                # This is a link to another node
                source_id = str(value[0])
                source_handle = reverse_map.get(comfy_key, comfy_key)
                edges.append({
                    "id": f"edge_{source_id}_{node_id}_{comfy_key}",
                    "source": source_id,
                    "target": node_id,
                    "sourceHandle": "0",
                    "targetHandle": source_handle,
                })
            else:
                flow_key = reverse_map.get(comfy_key, comfy_key)
                params[flow_key] = value

        nodes.append({
            "id": node_id,
            "type": flow_type,
            "position": {"x": 200, "y": len(nodes) * 150},
            "data": {
                "label": node_data.get("_meta", {}).get("title", class_type),
                "params": params,
                "status": "idle",
            },
        })

    return nodes, edges


def get_output_node_ids(prompt: dict[str, dict]) -> list[str]:
    """Identify output/preview nodes in a ComfyUI prompt."""
    output_types = {"PreviewImage", "SaveImage", "VHS_VideoCombine"}
    return [
        nid for nid, nd in prompt.items()
        if nd.get("class_type") in output_types
    ]
