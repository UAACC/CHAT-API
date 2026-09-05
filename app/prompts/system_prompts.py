"""
System prompts for the chat assistant.

Resolution order:
1. SYSTEM_PROMPT_FILE: path to a JSON file shaped {"en": "...", "zh": "..."}
2. SYSTEM_PROMPT_EN / SYSTEM_PROMPT_ZH environment variables
3. The generic defaults below

Per-site prompts live in deployment configs (see deployments/), not here.
"""

import json
import logging
from pathlib import Path
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_EN = """You are the friendly assistant for this website.

## Your Role
- Answer visitors' questions about the organisation, its offerings and how to get in touch
- Help visitors find the right page or the right next step
- Be warm, concise and professional

## Rules
- Only state facts that appear in the retrieved context or in this prompt. Never invent prices, dates, availability, names, policies or guarantees.
- If you do not know something, say so and point the visitor to the site's contact page.
- Keep answers to 2-4 sentences unless the visitor asks for detail.
- Reply in the language the visitor writes in.
- This is a general-inquiry assistant: remind visitors not to share sensitive or confidential information here.
"""

DEFAULT_PROMPT_ZH = """您是本网站的友好助手。

## 您的角色
- 回答访客关于本机构、其产品或服务以及联系方式的问题
- 帮助访客找到合适的页面或下一步行动
- 态度温和、简洁、专业

## 规则
- 只陈述检索到的资料或本提示词中出现的事实。绝不编造价格、日期、可用性、人名、政策或承诺。
- 不确定的问题要坦率说明，并引导访客前往网站的联系页面。
- 通常用 2-4 句话回答，访客要求详情时再展开。
- 用访客使用的语言回复。
- 这是一个通用咨询助手：提醒访客不要在此分享敏感或机密信息。
"""


@lru_cache
def load_prompts() -> dict:
    """
    Load system prompts from configuration.

    Returns:
        Dict with 'en' and 'zh' keys containing prompt strings
    """
    settings = get_settings()
    prompts = {}

    # Try loading from file first
    if settings.system_prompt_file:
        try:
            file_path = Path(settings.system_prompt_file)
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    prompts = json.load(f)
                    logger.info(f"Loaded prompts from file: {file_path}")
                    return prompts
        except Exception as e:
            logger.warning(f"Failed to load prompts from file: {e}")

    # Try environment variables
    if settings.system_prompt_en:
        prompts['en'] = settings.system_prompt_en
        logger.info("Loaded EN prompt from environment variable")

    if settings.system_prompt_zh:
        prompts['zh'] = settings.system_prompt_zh
        logger.info("Loaded ZH prompt from environment variable")

    # Fall back to generic defaults
    if 'en' not in prompts:
        prompts['en'] = DEFAULT_PROMPT_EN
    if 'zh' not in prompts:
        prompts['zh'] = DEFAULT_PROMPT_ZH

    return prompts


def get_system_prompt(locale: str) -> str:
    """
    Get the appropriate system prompt based on locale.

    Args:
        locale: Language locale ('en', 'zh', etc.)

    Returns:
        System prompt string in the requested language
    """
    prompts = load_prompts()

    if locale.startswith("zh"):
        return prompts.get('zh', prompts.get('en', DEFAULT_PROMPT_EN))

    return prompts.get('en', DEFAULT_PROMPT_EN)
