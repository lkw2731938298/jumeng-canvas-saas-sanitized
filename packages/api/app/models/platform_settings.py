from ..core.datetime_util import now_cst_naive
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from .database import Base
from .types import JsonCol


class PlatformSettings(Base):
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True, default=1)
    registration_enabled = Column(Boolean, nullable=False, default=True)
    homepage_background_mode = Column(String(32), nullable=False, default="default")
    homepage_background_oss_key = Column(Text, nullable=True)
    auth_grid_images = Column(JsonCol, nullable=False, default=list)
    # 登录弹窗左半边运营图（OSS key）；空则前端用占位背景
    login_modal_oss_key = Column(Text, nullable=True)
    # 登录弹窗法务文档：用户协议 / 隐私政策（OSS key + 元数据）
    legal_documents = Column(JsonCol, nullable=True)
    # 首次注册赠送：开关 + 算力点数（每人终身一次）
    register_bonus_enabled = Column(Boolean, nullable=False, default=False)
    register_bonus_amount = Column(Integer, nullable=False, default=0)
    # 全用户通用云存储配额（GiB），会员到期后仍适用
    default_storage_gb = Column(Integer, nullable=False, default=2)
    # 算力充值档位：[{creditsAmount, payAmountFen, sortOrder, isActive}]
    recharge_tiers = Column(JsonCol, nullable=True)
    # 画布工具固定算力：{version, tools:{multi_angle,lighting,panorama,grid_9,grid_split}}
    canvas_tool_pricing = Column(JsonCol, nullable=True)
    # Agent 编排轨 S：{version, sessionStart, skills:{slug:cost}}
    agent_skill_pricing = Column(JsonCol, nullable=True)
    # 画布/分镜各功能的模型切换（主/副模型）：{version, tools:{<toolId>:{primary,secondary}}}
    canvas_tool_models = Column(JsonCol, nullable=True)
    # 发现页运营内容：Hero / 提示词 / 技能亮点 / 作品广场等（与登录页背景隔离）
    discover_page = Column(JsonCol, nullable=True)
    # 首页 Footer：关于我们跳转 / 联系我们二维码 / 社交平台链接
    site_footer = Column(JsonCol, nullable=True)
    # 画布模型选择 UI 标签库：{version, tags:[{id,label,sortOrder,enabled,categories}]}
    model_ui_tags = Column(JsonCol, nullable=True)
    # 画布选模左侧「系列」展示顺序：{version, series:[{label,sortOrder,enabled,categories}]}
    model_ui_series = Column(JsonCol, nullable=True)
    # Skill MD 覆盖：单文件为字符串；多文件技能包为 {_package:true, files:{rel:md}}；未写入的 key 用仓库默认
    skill_docs = Column(JsonCol, nullable=True)
    # Skill 公开目录分类 Tab（不含「推荐」）：["通用技能", ...]
    skill_categories = Column(JsonCol, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=now_cst_naive, nullable=False)
