"""参考图 URL 解析与项目归属校验。"""

from app.services.project_scope import oss_key_belongs_to_project
from app.services.reference_image import (
    _is_internal_reference_url,
    resolve_reference_for_ark,
)


def test_presigned_oss_url_not_treated_as_internal():
    folder = "a1b2c3d4e5f6478990a1b2c3d4e5f678"
    presigned = (
        f"https://my-bucket.oss-cn-hangzhou.aliyuncs.com/Ihuabu/projects/{folder}/"
        f"assets/image/test.png?Expires=9999999999&OSSAccessKeyId=abc&Signature=def"
    )
    assert _is_internal_reference_url(presigned) is False


def test_double_resolve_presigned_url_without_storage_folder_ok():
    """dispatch 已预签名后，Ark 二次解析不应因缺 storage_folder 报跨项目。"""
    folder = "a1b2c3d4e5f6478990a1b2c3d4e5f678"
    presigned = (
        f"https://my-bucket.oss-cn-hangzhou.aliyuncs.com/Ihuabu/projects/{folder}/"
        f"assets/image/test.png?Expires=9999999999&OSSAccessKeyId=abc&Signature=def"
    )
    resolved = resolve_reference_for_ark(presigned, project_id="12345")
    assert resolved == presigned


def test_proxy_url_requires_storage_folder_for_hex_project_path():
    folder = "a1b2c3d4e5f6478990a1b2c3d4e5f678"
    key = f"Ihuabu/projects/{folder}/assets/image/test.png"
    proxy = f"/api/storage/object?key={key}"
    assert _is_internal_reference_url(proxy) is True
    assert oss_key_belongs_to_project(key, "12345", storage_folder=folder) is True
    assert oss_key_belongs_to_project(key, "12345", storage_folder=None) is False
