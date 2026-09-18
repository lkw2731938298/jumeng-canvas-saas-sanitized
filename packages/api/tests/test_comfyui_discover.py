from app.integrations.comfyui.discover import guess_media_category, slug_catalog_name
from app.integrations.comfyui.generic_prompt import apply_user_workflow, default_sd_txt2img_prompt


def test_guess_category_video_hint():
    assert guess_media_category("my_i2v_model.safetensors", "diffusion_models") == "video"
    assert guess_media_category("sdxl_base.safetensors", "checkpoints") == "image"


def test_slug_catalog_name_generic():
    name = slug_catalog_name("comfy", "SDXL Base 1.0.safetensors")
    assert name.startswith("comfy_")
    assert name[0].isalpha()
    assert all(c.isalnum() or c == "_" for c in name)


def test_apply_workflow_placeholders():
    wf = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "{{MODEL}}"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "{{PROMPT}}"},
        },
    }
    out = apply_user_workflow(wf, prompt="a cat", model_filename="mine.safetensors")
    assert out["1"]["inputs"]["ckpt_name"] == "mine.safetensors"
    assert out["2"]["inputs"]["text"] == "a cat"


def test_default_txt2img_uses_user_ckpt():
    prompt = default_sd_txt2img_prompt(ckpt_name="user_model.safetensors", prompt="hello")
    assert prompt["1"]["inputs"]["ckpt_name"] == "user_model.safetensors"
    assert prompt["2"]["inputs"]["text"] == "hello"
