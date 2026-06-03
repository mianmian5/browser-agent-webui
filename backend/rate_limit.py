"""
统一限流模块 — 每 IP 总共免费试用 N 次，用完需自行配置 API Key
"""
import json
import os
from pathlib import Path
from fastapi import HTTPException, Request

FREE_LIMIT = 5                      # 每 IP 总共免费用几次
DATA_FILE = Path("/tmp/rate-limits.json")   # 持久化文件


def _load_limits() -> dict[str, int]:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_limits(data: dict[str, int]):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False))


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(request: Request, limit: int = FREE_LIMIT) -> int:
    """
    检查 IP 是否超出免费额度。
    超出时抛出 429，附带配置自己 Key 的提示。
    返回剩余次数。
    """
    client_ip = _get_client_ip(request)
    limits = _load_limits()
    used = limits.get(client_ip, 0)

    if used >= limit:
        raise HTTPException(
            status_code=429,
            detail={
                "message": f"免费试用次数已用完（{limit}/{limit}）。"
                           "如需继续使用，请点击右上角 ⚙️ 配置自己的 API Key。",
                "code": "rate_limit_exceeded",
                "limit": limit,
                "used": used,
                "remaining": 0,
            },
        )

    limits[client_ip] = used + 1
    _save_limits(limits)
    return limit - used - 1


def get_remaining(request: Request) -> dict:
    """获取当前 IP 的剩余次数"""
    client_ip = _get_client_ip(request)
    limits = _load_limits()
    used = limits.get(client_ip, 0)
    remaining = max(0, FREE_LIMIT - used)
    return {
        "limit": FREE_LIMIT,
        "used": used,
        "remaining": remaining,
        "exceeded": used >= FREE_LIMIT,
    }
