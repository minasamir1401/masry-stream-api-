from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Genre, Media, media_genres
from app.schemas import CategoryWithCount

router = APIRouter(prefix="/api", tags=["Categories"])

DEFAULT_ANIME_CATEGORIES = [
    {"id": 1, "name": "أكشن", "slug": "action", "description": "أنميات الإثارة والمعارك الحماسية", "count": 120},
    {"id": 2, "name": "مغامرات", "slug": "adventure", "description": "رحلات استكشافية وعوالم خيالية", "count": 95},
    {"id": 3, "name": "شونين", "slug": "shounen", "description": "أنميات الشباب والقوة والتحدي", "count": 88},
    {"id": 4, "name": "خيال", "slug": "fantasy", "description": "سحر وعوالم غير مألوفة", "count": 76},
    {"id": 5, "name": "كوميديا", "slug": "comedy", "description": "مواقف طريفة ومضحكة", "count": 64},
    {"id": 6, "name": "دراما", "slug": "drama", "description": "قصص مؤثرة وعلاقات إنسانية عميقة", "count": 52},
    {"id": 7, "name": "خارق للطبيعة", "slug": "supernatural", "description": "قوى غامضة وكائنات أسطورية", "count": 48},
    {"id": 8, "name": "غموض", "slug": "mystery", "description": "ألغاز وتحقيقات مشوقة", "count": 35},
]


@router.get("/categories", response_model=List[CategoryWithCount])
async def list_categories(db: AsyncSession = Depends(get_db)):
    query = (
        select(
            Genre.id,
            Genre.name,
            Genre.slug,
            Genre.description,
            func.count(media_genres.c.media_id).label("media_count"),
        )
        .outerjoin(media_genres, Genre.id == media_genres.c.genre_id)
        .group_by(Genre.id, Genre.name, Genre.slug, Genre.description)
        .order_by(Genre.id.asc())
    )

    result = await db.execute(query)
    categories = []
    for row in result.all():
        categories.append(
            CategoryWithCount(
                id=row.id,
                name=row.name,
                slug=row.slug,
                description=row.description,
                count=row.media_count,
            )
        )

    if not categories:
        return [CategoryWithCount(**cat) for cat in DEFAULT_ANIME_CATEGORIES]

    return categories
