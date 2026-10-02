"""每日免费次数发放记录 — 保证「每人每天最多发一次」."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IntPKMixin


class DailyBonusLog(Base, IntPKMixin):
    """每天发放一次的免费次数流水（唯一键兜底并发）.

    (user_id, claim_date) 上的唯一约束是幂等的关键：
    即使多个 gunicorn worker / 并发请求同时判断出「今天还没发」，
    也只有一条 INSERT 能成功，其余会撞唯一键被忽略，
    因此不需要 Redis 锁，也不会重复发次数。
    """

    __tablename__ = "daily_bonus_logs"
    __table_args__ = (
        UniqueConstraint("user_id", "claim_date", name="uk_daily_bonus_user_date"),
    )

    user_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="用户ID"
    )
    claim_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="发放日期（按业务时区计算）"
    )
    bonus_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="本次发放的免费次数"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, comment="发放时间"
    )
