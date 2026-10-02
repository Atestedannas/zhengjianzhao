"""管理员数据看板."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_admin
from app.models.process_record import ProcessRecord
from app.models.order import Order
from app.models.user import User
from app.schemas.common import ResponseModel

router = APIRouter()


@router.get("/stats", response_model=ResponseModel)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """数据看板核心统计."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # 今日处理次数
    today_count_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(
            ProcessRecord.created_at >= today
        )
    )
    today_count = today_count_result.scalar() or 0

    # 总处理次数
    total_count_result = await db.execute(select(func.count(ProcessRecord.id)))
    total_count = total_count_result.scalar() or 0

    # 成功/失败率
    success_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(ProcessRecord.status == "success")
    )
    failed_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(ProcessRecord.status == "failed")
    )
    success_count = success_result.scalar() or 0
    failed_count = failed_result.scalar() or 0

    # 平均耗时
    avg_time_result = await db.execute(
        select(func.avg(ProcessRecord.processing_time_ms)).where(ProcessRecord.status == "success")
    )
    avg_time = avg_time_result.scalar() or 0

    # 今日收入
    today_income_result = await db.execute(
        select(func.coalesce(func.sum(Order.amount), 0)).where(
            Order.status == "paid",
            Order.paid_at >= today,
        )
    )
    today_income = float(today_income_result.scalar() or 0)

    # 今日付费订单数
    today_paid_result = await db.execute(
        select(func.count(Order.id)).where(
            Order.status == "paid",
            Order.paid_at >= today,
        )
    )
    today_paid_count = today_paid_result.scalar() or 0

    # 免费消耗占比
    free_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(
            ProcessRecord.is_paid == False,
            ProcessRecord.created_at >= today,
        )
    )
    free_count = free_result.scalar() or 0
    paid_result = await db.execute(
        select(func.count(ProcessRecord.id)).where(
            ProcessRecord.is_paid == True,
            ProcessRecord.created_at >= today,
        )
    )
    paid_proc_count = paid_result.scalar() or 0
    total_today_proc = free_count + paid_proc_count
    free_ratio = round(free_count / total_today_proc * 100, 1) if total_today_proc > 0 else 0

    # 用户统计
    active_users_result = await db.execute(
        select(func.count(User.id)).where(User.is_active == True)
    )
    active_users = active_users_result.scalar() or 0

    unlimited_users_result = await db.execute(
        select(func.count(User.id)).where(User.free_count == -1)
    )
    unlimited_users = unlimited_users_result.scalar() or 0

    return ResponseModel(
        code=200,
        data={
            "today_processed": today_count,
            "total_processed": total_count,
            "today_revenue": today_income,
            "paid_orders_count": today_paid_count,
            "free_usage_ratio": round(free_ratio / 100, 4),
            "rembg_fail_rate": round((failed_count / total_count * 100) / 100, 4) if total_count > 0 else 0,
            # 兼容旧字段
            "today_count": today_count,
            "total_count": total_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "failure_rate": round(failed_count / total_count * 100, 1) if total_count > 0 else 0,
            "avg_processing_time_ms": round(float(avg_time), 0),
            "today_income": today_income,
            "today_paid_orders": today_paid_count,
            "total_paid_orders": 0,
            "free_ratio": free_ratio,
            "active_users": active_users,
            "unlimited_users": unlimited_users,
            "total_processes": total_count,
        },
    )


@router.get("/trend", response_model=ResponseModel)
async def get_trend_data(
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """处理趋势图数据（默认最近7天）.

    返回格式与前端 admin/src/api/dashboard.ts 的 TrendData 对齐：
    { dates: string[], process_counts: number[], revenue_amounts: number[] }
    """
    dates = []
    process_counts = []
    revenue_amounts = []
    for i in range(days - 1, -1, -1):
        day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
        day_end = day_start + timedelta(days=1)

        cnt_result = await db.execute(
            select(func.count(ProcessRecord.id)).where(
                ProcessRecord.created_at >= day_start,
                ProcessRecord.created_at < day_end,
            )
        )
        income_result = await db.execute(
            select(func.coalesce(func.sum(Order.amount), 0)).where(
                Order.status == "paid",
                Order.paid_at >= day_start,
                Order.paid_at < day_end,
            )
        )
        dates.append(day_start.strftime("%Y-%m-%d"))
        process_counts.append(cnt_result.scalar() or 0)
        revenue_amounts.append(float(income_result.scalar() or 0))

    return ResponseModel(
        code=200,
        data={
            "dates": dates,
            "process_counts": process_counts,
            "revenue_amounts": revenue_amounts,
        },
    )


@router.get("/template-usage", response_model=ResponseModel)
async def get_template_usage(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """模板使用排行（按使用次数降序，最多 10 个）."""
    from app.models.template import SpecTemplate

    result = await db.execute(
        select(SpecTemplate.name, func.count(ProcessRecord.id))
        .outerjoin(SpecTemplate, ProcessRecord.template_id == SpecTemplate.id)
        .group_by(ProcessRecord.template_id)
        .order_by(func.count(ProcessRecord.id).desc())
        .limit(10)
    )
    rows = result.all()

    names = []
    counts = []
    for name, cnt in rows:
        names.append(name or "未知模板")
        counts.append(cnt or 0)

    return ResponseModel(code=200, data={"names": names, "counts": counts})
