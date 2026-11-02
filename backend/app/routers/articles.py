import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, false, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.deps import get_current_membership
from app.db.session import get_db
from app.models import Article, OrganizationMember
from app.schemas.articles import ArticleOut, ArticlePage

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("/suggestions", response_model=list[ArticleOut])
def suggest_articles(
    description: str = Query(min_length=3, max_length=250),
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> list[ArticleOut]:
    """Find plausible catalog matches, always within the current organization."""
    stopwords = {"the", "and", "for", "with", "der", "die", "das", "und", "mit", "für", "von"}
    words = list(dict.fromkeys(word for word in re.findall(r"[^\W_]+", description.lower())
                               if len(word) >= 3 and word not in stopwords))[:8]
    if not words:
        return []

    # Rank matches in the database before limiting results; avoid loading the entire catalog.
    matches = [Article.description.ilike(f"%{word}%") for word in words]
    score = sum((case((match, 1), else_=0) for match in matches), start=0)
    escaped = description.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    number_match = Article.article_number.ilike(f"%{escaped}%", escape="\\")
    # The supplied German moss quotes describe catalog items in English. This
    # intentionally small vocabulary produces candidates, not equivalence claims.
    moss_match = Article.description.ilike("%moss%") if any("moos" in word for word in words) else false()
    minimum = 2 if len(words) > 1 else 1
    stmt = (select(Article)
            .where(Article.organization_id == membership.organization_id,
                   or_(number_match, moss_match, score >= minimum))
            .options(joinedload(Article.supplier))
            .order_by(case((number_match, 1), else_=0).desc(), case((moss_match, 1), else_=0).desc(), score.desc(), Article.article_number)
            .limit(5))
    return [ArticleOut(
        id=a.id, article_number=a.article_number, supplier_id=a.supplier_id,
        supplier_name=a.supplier.name if a.supplier else None,
        description=a.description, unit_price=a.unit_price, currency=a.currency,
        unit=a.unit, quantity=a.quantity, created_at=a.created_at,
    ) for a in db.scalars(stmt)]


@router.get("", response_model=ArticlePage)
def list_articles(
    search: str | None = Query(
        default=None, description="Filter by description or article number (case-insensitive)"
    ),
    supplier_id: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> ArticlePage:
    filters = [Article.organization_id == membership.organization_id]
    if search:
        term = f"%{search}%"
        filters.append(
            or_(Article.description.ilike(term), Article.article_number.ilike(term))
        )
    if supplier_id:
        filters.append(Article.supplier_id == supplier_id)

    total = db.scalar(
        select(func.count()).select_from(Article).where(*filters)
    ) or 0

    stmt = (
        select(Article)
        .where(*filters)
        .options(joinedload(Article.supplier))
        .order_by(Article.article_number)
        .offset(offset)
        .limit(limit)
    )
    items = [
        ArticleOut(
            id=a.id,
            article_number=a.article_number,
            supplier_id=a.supplier_id,
            supplier_name=a.supplier.name if a.supplier else None,
            description=a.description,
            unit_price=a.unit_price,
            currency=a.currency,
            unit=a.unit,
            quantity=a.quantity,
            created_at=a.created_at,
        )
        for a in db.scalars(stmt)
    ]
    return ArticlePage(items=items, total=total, limit=limit, offset=offset)


@router.get("/count")
def count_articles(
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    total = (
        db.scalar(
            select(func.count())
            .select_from(Article)
            .where(Article.organization_id == membership.organization_id)
        )
        or 0
    )
    return {"count": total}


@router.get("/{article_id}", response_model=ArticleOut)
def get_article(
    article_id: uuid.UUID,
    membership: OrganizationMember = Depends(get_current_membership),
    db: Session = Depends(get_db),
) -> ArticleOut:
    article = db.scalar(select(Article).where(
        Article.id == article_id,
        Article.organization_id == membership.organization_id,
    ).options(joinedload(Article.supplier)))
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return ArticleOut(
        id=article.id, article_number=article.article_number, supplier_id=article.supplier_id,
        supplier_name=article.supplier.name if article.supplier else None,
        description=article.description, unit_price=article.unit_price, currency=article.currency,
        unit=article.unit, quantity=article.quantity, created_at=article.created_at,
    )
