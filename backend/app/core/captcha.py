"""图形验证码：Pillow 生成 PNG，答案存入缓存（不依赖服务端 session）。

由于认证走 JWT（无服务端 session），验证码答案以 ``captcha:{id}`` 为键写入
:class:`~app.core.cache.CacheBackend`，客户端仅持有随机 ``captcha_id``；
消费时「取后即删」，因此验证码天然一次性。
"""

from __future__ import annotations

import random
import secrets
from io import BytesIO

from PIL import Image, ImageDraw

from app.core.cache import CacheBackend

CAPTCHA_TTL_SECONDS = 300
"""验证码有效期（秒）。"""

CAPTCHA_LENGTH = 4
"""验证码字符数。"""

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去除易混淆的 I/O/0/1


def captcha_key(captcha_id: str) -> str:
    """返回验证码答案在缓存中的键名。"""
    return f"captcha:{captcha_id}"


def generate_captcha_text(length: int = CAPTCHA_LENGTH) -> str:
    """生成随机验证码文本（大写字母与数字）。"""
    return "".join(secrets.choice(_ALPHABET) for _ in range(max(3, length)))


def draw_captcha_png(text: str, width: int = 120, height: int = 44) -> bytes:
    """用 Pillow 绘制验证码 PNG 字节（含干扰点与干扰线）。"""
    rng = random.Random(secrets.randbits(64))
    image = Image.new("RGB", (width, height), (250, 250, 250))
    draw = ImageDraw.Draw(image)

    for _ in range(120):
        draw.point(
            (rng.randint(0, width), rng.randint(0, height)),
            fill=(rng.randint(120, 200), rng.randint(120, 200), rng.randint(120, 200)),
        )
    for _ in range(4):
        draw.line(
            (
                rng.randint(0, width),
                rng.randint(0, height),
                rng.randint(0, width),
                rng.randint(0, height),
            ),
            fill=(rng.randint(150, 210), rng.randint(150, 210), rng.randint(150, 210)),
            width=1,
        )

    char_width = width // max(1, len(text))
    for index, char in enumerate(text):
        x = 8 + index * char_width + rng.randint(-2, 2)
        y = rng.randint(6, 16)
        draw.text((x, y), char, fill=(30, 60, 140))

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


async def issue_captcha(cache: CacheBackend) -> tuple[str, bytes]:
    """生成验证码：返回 ``(captcha_id, png_bytes)``，答案写入缓存。"""
    text = generate_captcha_text()
    captcha_id = secrets.token_urlsafe(16)
    await cache.set(captcha_key(captcha_id), text.upper(), ttl=CAPTCHA_TTL_SECONDS)
    return captcha_id, draw_captcha_png(text)


async def consume_captcha(cache: CacheBackend, captcha_id: str, answer: str) -> bool:
    """校验并消费验证码；无论成败都删除，保证一次性。"""
    key = captcha_key(captcha_id)
    expected = await cache.get(key)
    await cache.delete(key)
    if not expected:
        return False
    return str(answer).strip().upper() == str(expected).upper()


__all__ = [
    "CAPTCHA_LENGTH",
    "CAPTCHA_TTL_SECONDS",
    "captcha_key",
    "consume_captcha",
    "draw_captcha_png",
    "generate_captcha_text",
    "issue_captcha",
]
