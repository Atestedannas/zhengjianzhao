"""模板服务：启动加载 presets.yaml 到数据库，Redis 缓存."""

import os
from typing import List, Optional

import yaml
from loguru import logger
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.template import SpecTemplate
from app.config import settings


def _load_yaml() -> list[dict]:
    """从 presets.yaml 加载模板数据."""
    yaml_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "templates_config",
        "presets.yaml",
    )
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("templates", [])


async def init_templates() -> None:
    """应用启动时调用：将 presets.yaml 中的模板同步到数据库.

    【幂等设计】spec_templates.name 没有唯一索引，MySQL 的
    on_duplicate_key_update 在没有唯一键冲突时等于纯 INSERT，
    会导致**每次启动都重复插入 4 条记录**。配合 gunicorn --max-requests
    的频繁重启，表会以每天 2000+ 行的速度膨胀（实测 6 小时 122 次重启
    → 1.4 万行垃圾数据 → /templates/ 接口要序列化 2.7MB、耗时 9.7s）。

    所以这里统一改成「先按 name 查，存在则更新、不存在才插入」，
    无论有没有唯一索引都不会重复灌数据。
    """
    from app.dependencies import async_session_factory

    templates = _load_yaml()
    logger.info(f"Loading {len(templates)} preset templates...")

    async with async_session_factory() as db:
        try:
            for tmpl in templates:
                values = dict(
                    name=tmpl["name"],
                    width_px=tmpl.get("width_px", 0),
                    height_px=tmpl.get("height_px", 0),
                    dpi=tmpl.get("dpi", 350),
                    min_kb=tmpl.get("min_kb", 0),
                    max_kb=tmpl.get("max_kb", 100),
                    allowed_bg_colors=tmpl.get("allowed_bg_colors", []),
                    output_format=tmpl.get("output_format", "JPEG"),
                    physical_size_mm=tmpl.get("physical_size_mm"),
                    is_active=tmpl.get("is_active", True),
                    remark=tmpl.get("remark", ""),
                )

                # 统一用「先查后写」：有唯一索引时是 1 次查询 + 1 次更新，
                # 没有时也不会插入重复行，两种库结构都幂等。
                result = await db.execute(
                    select(SpecTemplate).where(SpecTemplate.name == tmpl["name"])
                )
                existing = result.scalar_one_or_none()

                if existing is not None:
                    # 只在内容真的变了时才写，减少无谓的 binlog
                    changed = any(
                        getattr(existing, k) != v
                        for k, v in values.items()
                        if k != "name"
                    )
                    if changed:
                        for k, v in values.items():
                            if k != "name":
                                setattr(existing, k, v)
                else:
                    stmt = insert(SpecTemplate).values(**values)
                    await db.execute(stmt)

            await db.commit()
            logger.info("Templates synced to database.")
        except Exception as e:
            logger.error(f"Failed to init templates: {e}")
            await db.rollback()
            raise


async def get_active_templates(db: AsyncSession) -> List[SpecTemplate]:
    """获取所有启用的模板（优先从 Redis 缓存读取）."""
    from app.dependencies import redis_client as rc

    cache_key = "templates:active"
    if rc is not None:
        cached = await rc.get(cache_key)
        if cached:
            import json
            return [SpecTemplate(**item) for item in json.loads(cached)]

    result = await db.execute(
        select(SpecTemplate).where(SpecTemplate.is_active == True).order_by(SpecTemplate.id)
    )
    templates = list(result.scalars().all())

    if rc is not None:
        import json
        data = [
            {
                "id": t.id, "name": t.name, "width_px": t.width_px, "height_px": t.height_px,
                "dpi": t.dpi, "min_kb": t.min_kb, "max_kb": t.max_kb,
                "allowed_bg_colors": t.allowed_bg_colors, "output_format": t.output_format,
                "physical_size_mm": t.physical_size_mm, "remark": t.remark,
            }
            for t in templates
        ]
        await rc.setex(cache_key, 300, json.dumps(data, ensure_ascii=False))

    return templates


async def get_template_by_id(db: AsyncSession, template_id: int) -> Optional[SpecTemplate]:
    """按 ID 获取模板."""
    result = await db.execute(select(SpecTemplate).where(SpecTemplate.id == template_id))
    return result.scalar()


async def clear_template_cache() -> None:
    """清除模板缓存."""
    from app.dependencies import redis_client as rc

    if rc is not None:
        await rc.delete("templates:active")
        logger.info("Template cache cleared.")