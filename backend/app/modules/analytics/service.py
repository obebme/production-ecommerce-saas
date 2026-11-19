"""Read-only SQL analytics queries over orders, order_items, and inventory_stocks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, String, case, func, or_, select
from sqlalchemy import cast as sa_cast
from sqlalchemy.orm import Session

from app.core.database import ts_gte, ts_lt
from app.core.errors import BadRequestError
from app.core.pagination import PaginatedResponse, PaginationParams
from app.models.catalog import Category, Product
from app.models.inventory import InventoryStock, ProductVariant
from app.models.order import Order, OrderItem
from app.modules.analytics.schemas import (
    AnalyticsSummaryResponse,
    AnalyticsTimeseriesResponse,
    InventoryHealthAlertDTO,
    InventoryHealthResponse,
    InventoryHealthSummaryDTO,
    TimeseriesPointDTO,
    TopProductDTO,
    TopProductSortBy,
    TopProductsResponse,
)
from app.modules.catalog.service import compute_variant_availability
from app.modules.orders.schemas import OrderStatus

MAX_ANALYTICS_SPAN_DAYS = 366
DEFAULT_ANALYTICS_DAYS = 30
_MIN_ANALYTICS_DATE = date(1970, 1, 1)
_MAX_ANALYTICS_DATE = date(9998, 12, 31)
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CANONICAL_ORDER_STATUSES: tuple[OrderStatus, ...] = (
    "PENDING_PAYMENT",
    "CONFIRMED",
    "PROCESSING",
    "SHIPPED",
    "COMPLETED",
    "CANCELLED",
    "REFUNDED",
)


@dataclass(frozen=True, slots=True)
class AnalyticsWindow:
    """Validated UTC analytics query window [start_dt, end_exclusive)."""

    from_date: date
    to_date: date
    start_dt: datetime
    end_exclusive: datetime


def _validate_analytics_date(bound_date: date) -> None:
    if bound_date < _MIN_ANALYTICS_DATE or bound_date > _MAX_ANALYTICS_DATE:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message=(
                f"Date must be between {_MIN_ANALYTICS_DATE.isoformat()} "
                f"and {_MAX_ANALYTICS_DATE.isoformat()} UTC."
            ),
        )


def _parse_analytics_bound(raw: str, *, is_end: bool) -> tuple[datetime, date, bool]:
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Date parameter must not contain control characters.",
        )
    cleaned = raw.strip()
    if _DATE_ONLY_RE.fullmatch(cleaned):
        try:
            parsed_date = date.fromisoformat(cleaned)
        except ValueError as exc:
            raise BadRequestError(
                code="INVALID_DATE_RANGE",
                message=f"Invalid UTC date '{cleaned}'. Expected YYYY-MM-DD.",
            ) from exc
        _validate_analytics_date(parsed_date)
        if is_end:
            dt = datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                23,
                59,
                59,
                999999,
                tzinfo=UTC,
            )
        else:
            dt = datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                0,
                0,
                0,
                0,
                tzinfo=UTC,
            )
        return dt, parsed_date, True

    if "T" not in cleaned and " " not in cleaned and "t" not in cleaned:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Invalid date format. Use YYYY-MM-DD or ISO-8601 UTC datetime.",
        )

    try:
        parsed_dt = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Invalid ISO-8601 date/datetime parameter.",
        ) from exc

    try:
        utc_dt = (
            parsed_dt.replace(tzinfo=UTC)
            if parsed_dt.tzinfo is None
            else parsed_dt.astimezone(UTC)
        )
    except OverflowError as exc:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Date/datetime parameter is out of supported UTC calendar bounds.",
        ) from exc
    _validate_analytics_date(utc_dt.date())
    return utc_dt, utc_dt.date(), False


def _clean_optional_param(raw: str | None) -> str | None:
    if raw is None:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Date parameter must not contain control characters.",
        )
    cleaned = raw.strip()
    return cleaned if cleaned else None


def parse_analytics_window(
    from_raw: str | None,
    to_raw: str | None,
    *,
    today_utc: date | None = None,
) -> AnalyticsWindow:
    """Parse and validate optional UTC from/to params enforcing span <= 366 days."""
    cleaned_from = _clean_optional_param(from_raw)
    cleaned_to = _clean_optional_param(to_raw)
    resolved_today = today_utc or datetime.now(UTC).date()

    if cleaned_to is not None:
        to_dt_inclusive, to_date, to_is_date = _parse_analytics_bound(cleaned_to, is_end=True)
    else:
        to_date = resolved_today
        to_dt_inclusive = datetime(
            to_date.year, to_date.month, to_date.day, 23, 59, 59, 999999, tzinfo=UTC
        )
        to_is_date = True

    if cleaned_from is not None:
        start_dt, from_date, from_is_date = _parse_analytics_bound(cleaned_from, is_end=False)
    else:
        try:
            from_date = to_date - timedelta(days=DEFAULT_ANALYTICS_DAYS - 1)
        except OverflowError as exc:
            raise BadRequestError(
                code="INVALID_DATE_RANGE",
                message="Date range is out of supported UTC calendar bounds.",
            ) from exc
        start_dt = datetime(from_date.year, from_date.month, from_date.day, tzinfo=UTC)
        from_is_date = True

    if start_dt > to_dt_inclusive:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Start date must be less than or equal to end date.",
        )

    if from_is_date and to_is_date:
        if (to_date - from_date).days > MAX_ANALYTICS_SPAN_DAYS:
            raise BadRequestError(
                code="INVALID_DATE_RANGE",
                message=f"Date range cannot exceed {MAX_ANALYTICS_SPAN_DAYS} days.",
            )
    else:
        if (to_dt_inclusive - start_dt) > timedelta(days=MAX_ANALYTICS_SPAN_DAYS):
            raise BadRequestError(
                code="INVALID_DATE_RANGE",
                message=f"Date range cannot exceed {MAX_ANALYTICS_SPAN_DAYS} days.",
            )

    try:
        end_exclusive = (
            datetime(to_date.year, to_date.month, to_date.day, tzinfo=UTC) + timedelta(days=1)
            if to_is_date
            else to_dt_inclusive + timedelta(microseconds=1)
        )
    except OverflowError as exc:
        raise BadRequestError(
            code="INVALID_DATE_RANGE",
            message="Date range is out of supported UTC calendar bounds.",
        ) from exc
    return AnalyticsWindow(
        from_date=from_date,
        to_date=to_date,
        start_dt=start_dt,
        end_exclusive=end_exclusive,
    )


def get_analytics_summary(
    db: Session,
    *,
    from_raw: str | None = None,
    to_raw: str | None = None,
) -> AnalyticsSummaryResponse:
    """Compute gross/refunded/net revenue, paid order count, AOV, and status counts."""
    window = parse_analytics_window(from_raw, to_raw)

    rev_row = db.execute(
        select(
            func.coalesce(func.sum(Order.total_cents), 0),
            func.coalesce(
                func.sum(
                    case(
                        (Order.payment_status == "REFUNDED", Order.total_cents),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (Order.payment_status == "PAID", 1),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(
            Order.paid_at.is_not(None),
            ts_gte(db, Order.paid_at, window.start_dt),
            ts_lt(db, Order.paid_at, window.end_exclusive),
            Order.payment_status.in_(("PAID", "REFUNDED")),
        )
    ).one()

    gross_revenue_cents = int(rev_row[0] or 0)
    refunded_revenue_cents = int(rev_row[1] or 0)
    paid_orders_count = int(rev_row[2] or 0)
    net_revenue_cents = gross_revenue_cents - refunded_revenue_cents
    average_order_value_cents = (
        net_revenue_cents // paid_orders_count if paid_orders_count > 0 else 0
    )

    orders_by_status: dict[OrderStatus, int] = {
        status_name: 0 for status_name in CANONICAL_ORDER_STATUSES
    }
    status_rows = db.execute(
        select(Order.status, func.count(Order.id))
        .where(
            ts_gte(db, Order.placed_at, window.start_dt),
            ts_lt(db, Order.placed_at, window.end_exclusive),
        )
        .group_by(Order.status)
    ).all()
    for status_name, cnt in status_rows:
        if status_name in orders_by_status:
            orders_by_status[status_name] = int(cnt)

    return AnalyticsSummaryResponse(
        from_date=window.from_date.isoformat(),
        to_date=window.to_date.isoformat(),
        gross_revenue_cents=gross_revenue_cents,
        refunded_revenue_cents=refunded_revenue_cents,
        net_revenue_cents=net_revenue_cents,
        paid_orders_count=paid_orders_count,
        average_order_value_cents=average_order_value_cents,
        orders_by_status=orders_by_status,
    )


def get_analytics_timeseries(
    db: Session,
    *,
    from_raw: str | None = None,
    to_raw: str | None = None,
) -> AnalyticsTimeseriesResponse:
    """Return continuous zero-filled UTC daily net revenue and paid orders counts."""
    window = parse_analytics_window(from_raw, to_raw)

    dialect_name = db.get_bind().dialect.name
    day_expr: ColumnElement[Any]
    if dialect_name == "sqlite":
        day_expr = func.substr(sa_cast(Order.paid_at, String), 1, 10)
    else:
        day_expr = func.date(Order.paid_at.op("AT TIME ZONE")("UTC"))

    rows = db.execute(
        select(
            day_expr.label("utc_day"),
            func.coalesce(func.sum(Order.total_cents), 0),
            func.count(Order.id),
        )
        .where(
            Order.paid_at.is_not(None),
            ts_gte(db, Order.paid_at, window.start_dt),
            ts_lt(db, Order.paid_at, window.end_exclusive),
            Order.payment_status == "PAID",
        )
        .group_by(day_expr)
    ).all()

    metrics_by_day: dict[str, tuple[int, int]] = {}
    for utc_day, rev_sum, order_cnt in rows:
        day_key = utc_day.isoformat() if isinstance(utc_day, date) else str(utc_day)[:10]
        metrics_by_day[day_key] = (int(rev_sum or 0), int(order_cnt or 0))

    points: list[TimeseriesPointDTO] = []
    cursor = window.from_date
    while cursor <= window.to_date:
        day_key = cursor.isoformat()
        rev_cents, cnt = metrics_by_day.get(day_key, (0, 0))
        points.append(
            TimeseriesPointDTO(
                date=day_key,
                net_revenue_cents=rev_cents,
                paid_orders_count=cnt,
            )
        )
        if cursor >= window.to_date:
            break
        cursor += timedelta(days=1)

    return AnalyticsTimeseriesResponse(
        from_date=window.from_date.isoformat(),
        to_date=window.to_date.isoformat(),
        points=points,
    )


def get_analytics_top_products(
    db: Session,
    *,
    from_raw: str | None = None,
    to_raw: str | None = None,
    sort_by: TopProductSortBy = "revenue",
    limit: int = 10,
) -> TopProductsResponse:
    """Return top-selling variant snapshots across PAID orders in the UTC window."""
    window = parse_analytics_window(from_raw, to_raw)

    units_expr = func.coalesce(func.sum(OrderItem.quantity), 0)
    revenue_expr = func.coalesce(func.sum(OrderItem.line_total_cents), 0)

    order_clauses = (
        (units_expr.desc(), revenue_expr.desc(), OrderItem.sku_snapshot.asc())
        if sort_by == "units"
        else (revenue_expr.desc(), units_expr.desc(), OrderItem.sku_snapshot.asc())
    )

    rows = db.execute(
        select(
            OrderItem.variant_id,
            OrderItem.product_id,
            OrderItem.sku_snapshot,
            OrderItem.product_title_snapshot,
            OrderItem.variant_title_snapshot,
            units_expr,
            revenue_expr,
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            Order.paid_at.is_not(None),
            ts_gte(db, Order.paid_at, window.start_dt),
            ts_lt(db, Order.paid_at, window.end_exclusive),
            Order.payment_status == "PAID",
        )
        .group_by(
            OrderItem.variant_id,
            OrderItem.product_id,
            OrderItem.sku_snapshot,
            OrderItem.product_title_snapshot,
            OrderItem.variant_title_snapshot,
        )
        .order_by(*order_clauses)
        .limit(limit)
    ).all()

    items = [
        TopProductDTO(
            variant_id=variant_id,
            product_id=product_id,
            sku_snapshot=sku_snapshot,
            product_title_snapshot=product_title_snapshot,
            variant_title_snapshot=variant_title_snapshot,
            units_sold=int(units_sold or 0),
            revenue_cents=int(revenue_cents or 0),
        )
        for (
            variant_id,
            product_id,
            sku_snapshot,
            product_title_snapshot,
            variant_title_snapshot,
            units_sold,
            revenue_cents,
        ) in rows
    ]

    return TopProductsResponse(
        from_date=window.from_date.isoformat(),
        to_date=window.to_date.isoformat(),
        items=items,
    )


def get_inventory_health(
    db: Session,
    *,
    pagination: PaginationParams,
) -> InventoryHealthResponse:
    """Return active SKU inventory health summary counts and paginated low/out-of-stock alerts."""
    on_hand_expr = func.coalesce(InventoryStock.on_hand, 0)
    reserved_expr = func.coalesce(InventoryStock.reserved, 0)
    threshold_expr = func.coalesce(InventoryStock.low_stock_threshold, 5)
    raw_available_expr = on_hand_expr - reserved_expr
    available_expr = case((raw_available_expr > 0, raw_available_expr), else_=0)

    active_scope_filters: tuple[ColumnElement[bool], ...] = (
        Category.is_active.is_(True),
        Product.status == "ACTIVE",
        ProductVariant.is_active.is_(True),
    )
    alert_filter = or_(available_expr <= 0, available_expr <= threshold_expr)

    summary_row = db.execute(
        select(
            func.count(ProductVariant.id),
            func.coalesce(
                func.sum(case((available_expr <= 0, 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        ((available_expr > 0) & (available_expr <= threshold_expr), 1),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(func.sum(reserved_expr), 0),
            func.coalesce(
                func.sum(case((alert_filter, 1), else_=0)),
                0,
            ),
        )
        .select_from(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .where(*active_scope_filters)
    ).one()

    active_skus_count = int(summary_row[0] or 0)
    out_of_stock_count = int(summary_row[1] or 0)
    low_stock_count = int(summary_row[2] or 0)
    total_reserved_units = int(summary_row[3] or 0)
    alerts_total = int(summary_row[4] or 0)

    summary = InventoryHealthSummaryDTO(
        active_skus_count=active_skus_count,
        out_of_stock_count=out_of_stock_count,
        low_stock_count=low_stock_count,
        total_reserved_units=total_reserved_units,
    )

    if alerts_total == 0 or pagination.offset >= alerts_total:
        return InventoryHealthResponse(
            summary=summary,
            alerts=PaginatedResponse(
                items=[],
                total=alerts_total,
                page=pagination.page,
                page_size=pagination.page_size,
                has_next=False,
            ),
        )

    rows = db.execute(
        select(ProductVariant, Product, InventoryStock)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .outerjoin(InventoryStock, InventoryStock.variant_id == ProductVariant.id)
        .where(*active_scope_filters, alert_filter)
        .order_by(raw_available_expr.asc(), ProductVariant.sku.asc(), ProductVariant.id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    alert_items: list[InventoryHealthAlertDTO] = []
    for variant, product, stock in rows:
        on_hand, reserved, available, threshold, in_stock, low_stock = (
            compute_variant_availability(stock)
        )
        alert_items.append(
            InventoryHealthAlertDTO(
                variant_id=variant.id,
                product_id=product.id,
                sku=variant.sku,
                product_title=product.title,
                variant_title=variant.title,
                on_hand=on_hand,
                reserved=reserved,
                available_quantity=available,
                low_stock_threshold=threshold,
                in_stock=in_stock,
                low_stock=low_stock,
            )
        )

    return InventoryHealthResponse(
        summary=summary,
        alerts=PaginatedResponse(
            items=alert_items,
            total=alerts_total,
            page=pagination.page,
            page_size=pagination.page_size,
            has_next=(pagination.offset + len(alert_items)) < alerts_total,
        ),
    )
