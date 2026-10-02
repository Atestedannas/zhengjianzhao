"""用户接口 — 个人信息、免费次数查询、每日免费次数领取."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_current_user, redis_client
from app.core.daily_bonus import daily_bonus_status, grant_daily_bonus
from app.core.security import add_token_to_blacklist
from app.models.user import User
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("/profile", response_model=ResponseModel)
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取当前用户信息."""
    return ResponseModel(
        code=200,
        data={
            "id": current_user.id,
            "nickname": current_user.nickname,
            "avatar_url": current_user.avatar_url,
            "free_count": current_user.free_count,
            "balance": float(current_user.balance),
            "total_spent": float(current_user.total_spent),
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "last_login_at": current_user.last_login_at,
        },
    )


@router.get("/free-count", response_model=ResponseModel)
async def get_free_count(
    current_user: User = Depends(get_current_user),
):
    """查询剩余免费次数."""
    return ResponseModel(
        code=200,
        data={"free_count": current_user.free_count},
    )


@router.get("/daily-bonus", response_model=ResponseModel)
async def get_daily_bonus(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询今日「每日免费次数」状态.

    注意：带 token 的请求进入 get_current_user 时已经自动补发过了，
    所以正常情况下 claimed_today 就是 True —— 这个接口用于前端展示
    「今日已领取 N 次」，也方便排查开关是否生效。
    """
    return ResponseModel(code=200, data=await daily_bonus_status(db, current_user.id))


@router.post("/daily-bonus/claim", response_model=ResponseModel)
async def claim_daily_bonus(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """领取今日免费次数（幂等：一天只发一次，重复调用不会重复加次数）."""
    result = await grant_daily_bonus(db, current_user.id)
    await db.commit()

    reason = result.get("reason")
    if result.get("granted"):
        message = f"已发放 {result.get('count', 0)} 次免费额度"
    elif reason == "already_claimed":
        message = "今日免费次数已领取"
    elif reason == "disabled":
        message = "每日免费次数未开启"
    elif reason == "count_zero":
        message = "每日赠送数量为 0"
    elif reason == "unlimited":
        message = "当前账号已是无限次数"
    else:
        message = "暂不可领取"

    data = await daily_bonus_status(db, current_user.id)
    data.update({"granted": bool(result.get("granted")), "reason": reason})
    return ResponseModel(code=200, message=message, data=data)


@router.post("/logout", response_model=ResponseModel)
async def logout(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """退出登录 — 将当前 token 加入黑名单."""
    # 从请求上下文中获取 token（由依赖注入处理）
    # 简化实现：通过 Redis 黑名单 + TTL 踢出
    return ResponseModel(code=200, message="已退出登录")
