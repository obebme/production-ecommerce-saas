"""Tests for Module 2 storefront and admin product catalog and category endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests.conftest import DEFAULT_ORIGIN, AuthenticatedSession

from app.core.errors import ConflictError
from app.models.catalog import Category, Product, ProductImage
from app.models.identity import AuditLog
from app.models.inventory import InventoryStock, ProductVariant
from app.modules.catalog.schemas import CreateCategoryRequest, validate_product_image_url
from app.modules.catalog.service import create_category


def _csrf_headers(session: AuthenticatedSession) -> dict[str, str]:
    return {"Origin": DEFAULT_ORIGIN, "X-CSRF-Token": session.csrf_token}


def _seed_category(
    db_session: Session,
    *,
    name: str,
    slug: str,
    parent_id: uuid.UUID | None = None,
    description: str | None = None,
    is_active: bool = True,
    sort_order: int = 0,
) -> Category:
    category = Category(
        parent_id=parent_id,
        name=name,
        slug=slug,
        description=description,
        is_active=is_active,
        sort_order=sort_order,
    )
    db_session.add(category)
    db_session.commit()
    db_session.refresh(category)
    return category


def _seed_product(
    db_session: Session,
    *,
    category_id: uuid.UUID,
    title: str,
    slug: str,
    description: str = "",
    status: str = "ACTIVE",
    created_at: datetime | None = None,
    images: list[tuple[str, str, int, bool]] | None = None,
    variants: list[dict[str, Any]] | None = None,
) -> tuple[Product, list[ProductVariant]]:
    product = Product(
        category_id=category_id,
        title=title,
        slug=slug,
        description=description,
        status=status,
    )
    if created_at is not None:
        product.created_at = created_at
        product.updated_at = created_at
    db_session.add(product)
    db_session.flush()

    for url, alt_text, sort_order, is_primary in images or []:
        db_session.add(
            ProductImage(
                product_id=product.id,
                url=url,
                alt_text=alt_text,
                sort_order=sort_order,
                is_primary=is_primary,
            )
        )

    created_variants: list[ProductVariant] = []
    for idx, v in enumerate(variants or []):
        variant = ProductVariant(
            product_id=product.id,
            sku=v["sku"],
            title=v.get("title", f"Variant {idx + 1}"),
            attributes_json=v.get("attributes", {}),
            price_cents=v["price_cents"],
            compare_at_price_cents=v.get("compare_at_price_cents"),
            currency=v.get("currency", "USD"),
            is_active=v.get("is_active", True),
        )
        if created_at is not None:
            variant.created_at = created_at + timedelta(seconds=idx)
            variant.updated_at = variant.created_at
        db_session.add(variant)
        db_session.flush()
        db_session.add(
            InventoryStock(
                variant_id=variant.id,
                on_hand=v.get("on_hand", 10),
                reserved=v.get("reserved", 0),
                low_stock_threshold=v.get("low_stock_threshold", 5),
                version=1,
            )
        )
        created_variants.append(variant)

    db_session.commit()
    db_session.refresh(product)
    return product, created_variants


def _count_sql_queries(db_session: Session, fn: Callable[[], Any]) -> tuple[int, Any]:
    engine = db_session.get_bind()
    statements: list[str] = []

    def _before_cursor_execute(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = statement.lstrip().upper()
        if not normalized.startswith(("BEGIN", "COMMIT", "ROLLBACK", "PRAGMA")):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)
    return len(statements), result


def test_public_categories_returns_active_only_sorted_without_auth(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /categories is public, excludes inactive rows, and sorts by sort_order ASC, name ASC."""
    parent = _seed_category(db_session, name="Apparel", slug="apparel", sort_order=20)
    _seed_category(
        db_session,
        name="Zebra Gear",
        slug="zebra-gear",
        sort_order=10,
        description="Second alphabetically at sort_order=10",
    )
    _seed_category(
        db_session,
        name="Alpha Gear",
        slug="alpha-gear",
        sort_order=10,
        description="First alphabetically at sort_order=10",
    )
    _seed_category(
        db_session,
        name="T-Shirts",
        slug="t-shirts",
        parent_id=parent.id,
        sort_order=30,
    )
    _seed_category(
        db_session,
        name="Hidden Season",
        slug="hidden-season",
        is_active=False,
        sort_order=-1,
    )

    resp = client.get("/api/v1/categories")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [item["slug"] for item in items] == [
        "alpha-gear",
        "zebra-gear",
        "apparel",
        "t-shirts",
    ]
    assert items[3]["parent_id"] == str(parent.id)
    assert "is_active" not in items[0]


def test_public_products_visibility_descendant_category_price_stock_sort_and_pagination(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /products enforces visibility rules, descendant categories, filters, sort, and paging."""
    base_time = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    apparel = _seed_category(db_session, name="Apparel", slug="apparel", sort_order=1)
    tops = _seed_category(
        db_session, name="Tops", slug="tops", parent_id=apparel.id, sort_order=2
    )
    tees = _seed_category(
        db_session, name="Tees", slug="tees", parent_id=tops.id, sort_order=3
    )
    inactive_child = _seed_category(
        db_session,
        name="Discontinued",
        slug="discontinued",
        parent_id=apparel.id,
        is_active=False,
    )
    accessories = _seed_category(
        db_session, name="Accessories", slug="accessories", sort_order=4
    )

    # Visible product in grandparent category (multi-variant: min=2500, max=6000, in_stock=True)
    _seed_product(
        db_session,
        category_id=apparel.id,
        title="Beta Jacket",
        slug="beta-jacket",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=1),
        images=[
            ("/assets/jacket-side.webp", "Side", 1, False),
            ("https://cdn.example.com/jacket-main.webp", "Main", 2, True),
        ],
        variants=[
            {"sku": "JKT-S", "price_cents": 2500, "on_hand": 0, "reserved": 0},
            {"sku": "JKT-L", "price_cents": 6000, "on_hand": 4, "reserved": 1},
        ],
    )
    # Visible product in grandchild category (out of stock due to reserved == on_hand)
    _seed_product(
        db_session,
        category_id=tees.id,
        title="alpha tee",
        slug="alpha-tee",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=3),
        variants=[{"sku": "TEE-BLK", "price_cents": 1500, "on_hand": 3, "reserved": 3}],
    )
    # Visible product in sibling category (in stock, highest price)
    _seed_product(
        db_session,
        category_id=accessories.id,
        title="Gamma Bag",
        slug="gamma-bag",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=2),
        variants=[{"sku": "BAG-01", "price_cents": 9500, "on_hand": 5, "reserved": 0}],
    )
    # Excluded: DRAFT, ARCHIVED, inactive category, inactive variants only, zero variants
    _seed_product(
        db_session,
        category_id=apparel.id,
        title="Draft Coat",
        slug="draft-coat",
        status="DRAFT",
        created_at=base_time + timedelta(minutes=10),
        variants=[{"sku": "DFT-01", "price_cents": 1000, "on_hand": 10}],
    )
    _seed_product(
        db_session,
        category_id=apparel.id,
        title="Archived Coat",
        slug="archived-coat",
        status="ARCHIVED",
        created_at=base_time + timedelta(minutes=11),
        variants=[{"sku": "ARC-01", "price_cents": 1000, "on_hand": 10}],
    )
    _seed_product(
        db_session,
        category_id=inactive_child.id,
        title="Hidden Child Item",
        slug="hidden-child-item",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=12),
        variants=[{"sku": "HID-01", "price_cents": 1000, "on_hand": 10}],
    )
    _seed_product(
        db_session,
        category_id=apparel.id,
        title="Inactive Variants Only",
        slug="inactive-variants-only",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=13),
        variants=[{"sku": "INV-01", "price_cents": 1000, "on_hand": 10, "is_active": False}],
    )
    _seed_product(
        db_session,
        category_id=apparel.id,
        title="No Variants Product",
        slug="no-variants-product",
        status="ACTIVE",
        created_at=base_time + timedelta(minutes=14),
        variants=[],
    )

    # Default sort=newest returns only the 3 eligible ACTIVE products
    all_resp = client.get("/api/v1/products")
    assert all_resp.status_code == 200
    all_data = all_resp.json()
    assert all_data["total"] == 3
    assert [p["slug"] for p in all_data["items"]] == [
        "alpha-tee",
        "gamma-bag",
        "beta-jacket",
    ]
    beta_card = all_data["items"][2]
    assert beta_card["min_price_cents"] == 2500
    assert beta_card["max_price_cents"] == 6000
    assert beta_card["in_stock"] is True
    assert beta_card["primary_image"]["url"] == "https://cdn.example.com/jacket-main.webp"

    # Descendant category filter includes apparel + tops + tees, excludes inactive_child & sibling
    cat_resp = client.get("/api/v1/products", params={"category_slug": "apparel"})
    assert cat_resp.status_code == 200
    assert [p["slug"] for p in cat_resp.json()["items"]] == ["alpha-tee", "beta-jacket"]

    # Unknown or inactive category_slug returns empty page cleanly
    discontinued_resp = client.get("/api/v1/products", params={"category_slug": "discontinued"})
    assert discontinued_resp.json()["total"] == 0
    nonexistent_resp = client.get("/api/v1/products", params={"category_slug": "nonexistent"})
    assert nonexistent_resp.json()["total"] == 0

    # Price range matches products with at least one active variant inside [min, max]
    price_resp = client.get(
        "/api/v1/products",
        params={"min_price_cents": 2000, "max_price_cents": 7000},
    )
    assert [p["slug"] for p in price_resp.json()["items"]] == ["beta-jacket"]

    # in_stock filtering
    in_stock_resp = client.get("/api/v1/products", params={"in_stock": "true"})
    assert {p["slug"] for p in in_stock_resp.json()["items"]} == {"beta-jacket", "gamma-bag"}
    oos_resp = client.get("/api/v1/products", params={"in_stock": "false"})
    assert [p["slug"] for p in oos_resp.json()["items"]] == ["alpha-tee"]

    # Sort modes: price_asc, price_desc, title_asc (case-insensitive)
    asc_items = client.get("/api/v1/products", params={"sort": "price_asc"}).json()["items"]
    assert [p["slug"] for p in asc_items] == ["alpha-tee", "beta-jacket", "gamma-bag"]
    desc_items = client.get("/api/v1/products", params={"sort": "price_desc"}).json()["items"]
    assert [p["slug"] for p in desc_items] == ["gamma-bag", "beta-jacket", "alpha-tee"]
    title_items = client.get("/api/v1/products", params={"sort": "title_asc"}).json()["items"]
    assert [p["slug"] for p in title_items] == ["alpha-tee", "beta-jacket", "gamma-bag"]

    # Pagination
    p1 = client.get("/api/v1/products", params={"page": 1, "page_size": 2}).json()
    assert p1["total"] == 3 and p1["has_next"] is True and len(p1["items"]) == 2
    p2 = client.get("/api/v1/products", params={"page": 2, "page_size": 2}).json()
    assert p2["total"] == 3 and p2["has_next"] is False and len(p2["items"]) == 1
    p3 = client.get("/api/v1/products", params={"page": 3, "page_size": 2}).json()
    assert p3["total"] == 3 and p3["has_next"] is False and p3["items"] == []

    # Invalid pagination bounds -> 422
    assert client.get("/api/v1/products", params={"page": 0}).status_code == 422
    assert client.get("/api/v1/products", params={"page_size": 101}).status_code == 422


def test_public_products_search_escapes_sql_wildcards_and_matches_title_description_sku(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /products search `q` matches title/description/active SKU and escapes %, _, \\."""
    cat = _seed_category(db_session, name="Fabrics", slug="fabrics")

    _seed_product(
        db_session,
        category_id=cat.id,
        title="100% Organic Cotton Tee",
        slug="cotton-100-pct",
        description="Soft brushed knit.",
        variants=[{"sku": "COT-100", "price_cents": 2000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="1000 Thread Shirt",
        slug="shirt-1000",
        description="Near miss for % wildcard.",
        variants=[{"sku": "SHRT-1000", "price_cents": 2000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Underscore Hoodie",
        slug="underscore-hoodie",
        description="Special tag edition.",
        variants=[
            {"sku": "HOOD_V1", "price_cents": 4000, "on_hand": 5},
            {"sku": "HIDDEN_INACTIVE_SKU", "price_cents": 4500, "on_hand": 5, "is_active": False},
        ],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Wildcard Near Miss Hoodie",
        slug="near-miss-hoodie",
        description="Near miss for _ wildcard.",
        variants=[{"sku": "HOODXV1", "price_cents": 4000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Backslash Jacket",
        slug="backslash-jacket",
        description=r"Series A\B waterproof shell",
        variants=[{"sku": "JKT-AB", "price_cents": 7000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Plain AB Jacket",
        slug="plain-ab-jacket",
        description="Series AB regular shell",
        variants=[{"sku": "JKT-PLAIN", "price_cents": 7000, "on_hand": 5}],
    )

    # Literal '%' must match "100%" only, not "1000"
    pct_resp = client.get("/api/v1/products", params={"q": "100%"})
    assert pct_resp.status_code == 200
    assert [p["slug"] for p in pct_resp.json()["items"]] == ["cotton-100-pct"]

    # Literal '_' in active SKU must match "HOOD_V1" only, not "HOODXV1"
    underscore_resp = client.get("/api/v1/products", params={"q": "hood_v1"})
    assert underscore_resp.status_code == 200
    assert [p["slug"] for p in underscore_resp.json()["items"]] == ["underscore-hoodie"]

    # Literal '\' in description must match "A\B" only, not "AB"
    backslash_resp = client.get("/api/v1/products", params={"q": r"a\b"})
    assert backslash_resp.status_code == 200
    assert [p["slug"] for p in backslash_resp.json()["items"]] == ["backslash-jacket"]

    # Inactive variant SKU must not leak into storefront search results
    inactive_sku_resp = client.get("/api/v1/products", params={"q": "HIDDEN_INACTIVE_SKU"})
    assert inactive_sku_resp.status_code == 200
    assert inactive_sku_resp.json()["items"] == []

    # Control chars rejected
    ctrl_resp = client.get("/api/v1/products", params={"q": "bad\x00query"})
    assert ctrl_resp.status_code == 422
    assert ctrl_resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_public_products_list_and_detail_bounded_query_budget_no_n_plus_1(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /products and GET /products/{slug} maintain bounded SQL query counts (no N+1)."""
    cat = _seed_category(db_session, name="Audio", slug="audio")
    base_time = datetime(2026, 11, 18, 10, 0, 0, tzinfo=UTC)

    def _create_batch(start: int, count: int) -> None:
        for i in range(start, start + count):
            _seed_product(
                db_session,
                category_id=cat.id,
                title=f"Speaker {i:02d}",
                slug=f"speaker-{i:02d}",
                created_at=base_time + timedelta(minutes=i),
                images=[
                    (f"/assets/s-{i}-1.webp", "img1", 0, True),
                    (f"/assets/s-{i}-2.webp", "img2", 1, False),
                ],
                variants=[
                    {"sku": f"SPK-{i}-A", "price_cents": 10000 + i, "on_hand": 5},
                    {"sku": f"SPK-{i}-B", "price_cents": 12000 + i, "on_hand": 3},
                ],
            )

    _create_batch(1, 2)
    q_count_small, small_resp = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products", params={"page_size": 50}),
    )
    assert small_resp.status_code == 200
    assert small_resp.json()["total"] == 2

    _create_batch(3, 13)
    q_count_large, large_resp = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products", params={"page_size": 50}),
    )
    assert large_resp.status_code == 200
    assert large_resp.json()["total"] == 15
    assert q_count_small <= 4
    assert q_count_large == q_count_small

    # With category_slug adds at most 1 category-tree lookup query + 1 reclaim probe, still constant
    q_count_cat, cat_resp = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products", params={"category_slug": "audio", "page_size": 50}),
    )
    assert cat_resp.status_code == 200
    assert q_count_cat <= 5

    # Detail endpoint runs in 1 joined query + 1 variant-scoped reclaim check
    q_count_detail, detail_resp = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products/speaker-01"),
    )
    assert detail_resp.status_code == 200
    assert q_count_detail == 2


def test_public_product_detail_returns_active_variants_availability_and_404_when_hidden(
    client: TestClient,
    db_session: Session,
) -> None:
    """GET /products/{slug} computes availability, orders images, and hides non-public products."""
    active_cat = _seed_category(db_session, name="Footwear", slug="footwear")
    inactive_cat = _seed_category(
        db_session, name="Archive Cat", slug="archive-cat", is_active=False
    )

    _seed_product(
        db_session,
        category_id=active_cat.id,
        title="Trail Runner",
        slug="trail-runner",
        description="Grip sole",
        status="ACTIVE",
        created_at=datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC),
        images=[
            ("https://cdn.example.com/runner-2.webp", "Side", 20, False),
            ("https://cdn.example.com/runner-1.webp", "Front", 5, True),
        ],
        variants=[
            {
                "sku": "RUN-9",
                "title": "Size 9",
                "attributes": {"size": "9"},
                "price_cents": 12000,
                "compare_at_price_cents": 14000,
                "on_hand": 8,
                "reserved": 5,
                "low_stock_threshold": 3,
                "is_active": True,
            },
            {
                "sku": "RUN-10",
                "title": "Size 10",
                "price_cents": 12000,
                "on_hand": 10,
                "reserved": 0,
                "low_stock_threshold": 2,
                "is_active": True,
            },
            {
                "sku": "RUN-11-OFF",
                "title": "Size 11",
                "price_cents": 12000,
                "on_hand": 10,
                "is_active": False,
            },
        ],
    )
    _seed_product(
        db_session,
        category_id=active_cat.id,
        title="Draft Boot",
        slug="draft-boot",
        status="DRAFT",
        variants=[{"sku": "BOT-D", "price_cents": 15000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=active_cat.id,
        title="Archived Boot",
        slug="archived-boot",
        status="ARCHIVED",
        variants=[{"sku": "BOT-A", "price_cents": 15000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=inactive_cat.id,
        title="Hidden Cat Shoe",
        slug="hidden-cat-shoe",
        status="ACTIVE",
        variants=[{"sku": "HID-S", "price_cents": 9000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=active_cat.id,
        title="All Variants Inactive",
        slug="all-variants-inactive",
        status="ACTIVE",
        variants=[{"sku": "OFF-ONLY", "price_cents": 8000, "on_hand": 5, "is_active": False}],
    )

    # Equal-timestamp variants must deterministically tie-break by SKU ascending
    tie_ts = datetime(2026, 11, 18, 12, 0, 0, tzinfo=UTC)
    tie_prod = Product(
        category_id=active_cat.id,
        title="Tie Sort Boot",
        slug="tie-sort-boot",
        status="ACTIVE",
        created_at=tie_ts,
        updated_at=tie_ts,
    )
    db_session.add(tie_prod)
    db_session.flush()
    for sku_code in ("SKU-Z", "SKU-A"):
        v = ProductVariant(
            product_id=tie_prod.id,
            sku=sku_code,
            title=f"Title {sku_code}",
            attributes_json={},
            price_cents=5000,
            currency="USD",
            is_active=True,
            created_at=tie_ts,
            updated_at=tie_ts,
        )
        db_session.add(v)
        db_session.flush()
        db_session.add(InventoryStock(variant_id=v.id, on_hand=5, reserved=0, version=1))
    db_session.commit()

    resp = client.get("/api/v1/products/trail-runner")
    assert resp.status_code == 200
    body = resp.json()
    assert body["slug"] == "trail-runner"
    assert body["category"]["slug"] == "footwear"
    assert [img["sort_order"] for img in body["images"]] == [5, 20]
    assert [v["sku"] for v in body["variants"]] == ["RUN-9", "RUN-10"]
    v9, v10 = body["variants"]
    assert v9["available_quantity"] == 3
    assert v9["in_stock"] is True
    assert v9["low_stock"] is True
    assert v9["attributes"] == {"size": "9"}
    assert v10["available_quantity"] == 10
    assert v10["low_stock"] is False

    tie_resp = client.get("/api/v1/products/tie-sort-boot")
    assert tie_resp.status_code == 200
    assert [v["sku"] for v in tie_resp.json()["variants"]] == ["SKU-A", "SKU-Z"]

    for hidden_slug in (
        "unknown-slug",
        "draft-boot",
        "archived-boot",
        "hidden-cat-shoe",
        "all-variants-inactive",
    ):
        not_found = client.get(f"/api/v1/products/{hidden_slug}")
        assert not_found.status_code == 404
        assert not_found.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


def test_public_and_admin_catalog_pagination_bounds_short_circuit_and_slug_validation(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Pagination bounds (422), offset >= total short-circuit, and pre-query slug checks."""
    cat = _seed_category(db_session, name="Bounds Cat", slug="bounds-cat")
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Bound Item 1",
        slug="bound-item-1",
        variants=[{"sku": "BND-1", "price_cents": 1000, "on_hand": 5}],
    )
    _seed_product(
        db_session,
        category_id=cat.id,
        title="Bound Item 2",
        slug="bound-item-2",
        variants=[{"sku": "BND-2", "price_cents": 2000, "on_hand": 5}],
    )

    # Bounded page validation (MAX_PAGINATION_PAGE = 1_000_000)
    bad_public_page = client.get("/api/v1/products", params={"page": 1_000_001})
    assert bad_public_page.status_code == 422
    assert bad_public_page.json()["error"]["code"] == "VALIDATION_ERROR"

    # offset >= total short-circuits after reclaim probe + COUNT without querying rows
    pub_queries, pub_page_50 = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products", params={"page": 50, "page_size": 20}),
    )
    assert pub_page_50.status_code == 200
    assert pub_page_50.json() == {
        "items": [],
        "total": 2,
        "page": 50,
        "page_size": 20,
        "has_next": False,
    }
    assert pub_queries == 2

    # Pre-query slug & category_slug validation rejects NUL/overlong slugs with 0 SQL queries
    nul_detail_queries, nul_detail = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products/item%00"),
    )
    assert nul_detail.status_code == 404
    assert nul_detail.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert nul_detail_queries == 0

    overlong_slug = "a" * 221
    long_detail_queries, long_detail = _count_sql_queries(
        db_session,
        lambda: client.get(f"/api/v1/products/{overlong_slug}"),
    )
    assert long_detail.status_code == 404
    assert long_detail.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert long_detail_queries == 0

    cat_nul_queries, cat_nul_resp = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/products?category_slug=bad%00cat"),
    )
    assert cat_nul_resp.status_code == 200
    assert cat_nul_resp.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
        "has_next": False,
    }
    assert cat_nul_queries == 1

    # Admin products page bound & offset >= total short-circuit
    login_as(client, role="staff")
    bad_admin_page = client.get("/api/v1/admin/products", params={"page": 1_000_001})
    assert bad_admin_page.status_code == 422
    assert bad_admin_page.json()["error"]["code"] == "VALIDATION_ERROR"

    admin_queries, admin_page_50 = _count_sql_queries(
        db_session,
        lambda: client.get("/api/v1/admin/products", params={"page": 50, "page_size": 20}),
    )
    assert admin_page_50.status_code == 200
    assert admin_page_50.json() == {
        "items": [],
        "total": 2,
        "page": 50,
        "page_size": 20,
        "has_next": False,
    }
    assert admin_queries <= 2  # 1 auth session lookup + 1 COUNT query (no row query)


def test_admin_categories_rbac_csrf_origin_crud_conflicts_cycles_and_audit(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Admin categories enforce RBAC/CSRF/Origin, slug/parent/cycle checks, and audit logs."""
    # Unauthenticated -> 401
    assert client.get("/api/v1/admin/categories").status_code == 401
    unauth_post = client.post(
        "/api/v1/admin/categories",
        json={"name": "Anon", "slug": "anon"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert unauth_post.status_code == 401
    assert unauth_post.json()["error"]["code"] == "AUTH_REQUIRED"

    # Customer -> 403 FORBIDDEN_ROLE
    cust_session = login_as(client, role="customer")
    assert client.get("/api/v1/admin/categories").status_code == 403
    cust_post = client.post(
        "/api/v1/admin/categories",
        json={"name": "Cust", "slug": "cust"},
        headers=_csrf_headers(cust_session),
    )
    assert cust_post.status_code == 403
    assert cust_post.json()["error"]["code"] == "FORBIDDEN_ROLE"

    # Staff allowed; CSRF and Origin enforced on mutations
    staff_session = login_as(client, role="staff")
    no_csrf = client.post(
        "/api/v1/admin/categories",
        json={"name": "No CSRF", "slug": "no-csrf"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    for bad_origin in ("https://evil.example.com", "null"):
        bad_origin_resp = client.post(
            "/api/v1/admin/categories",
            json={"name": "Bad Origin", "slug": "bad-origin"},
            headers={"Origin": bad_origin, "X-CSRF-Token": staff_session.csrf_token},
        )
        assert bad_origin_resp.status_code == 403
        assert bad_origin_resp.json()["error"]["code"] == "CSRF_INVALID"

    # Create category hierarchy: A -> B -> C
    create_a = client.post(
        "/api/v1/admin/categories",
        json={"name": "Cat A", "slug": "cat-a", "sort_order": 1},
        headers=_csrf_headers(staff_session),
    )
    assert create_a.status_code == 201, create_a.text
    cat_a = create_a.json()

    # Duplicate slug -> 409 SLUG_CONFLICT
    dup_resp = client.post(
        "/api/v1/admin/categories",
        json={"name": "Cat A Dup", "slug": "CAT-A"},
        headers=_csrf_headers(staff_session),
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "SLUG_CONFLICT"

    # Missing parent_id -> 404 CATEGORY_NOT_FOUND
    missing_parent = client.post(
        "/api/v1/admin/categories",
        json={"name": "Orphan", "slug": "orphan", "parent_id": str(uuid.uuid4())},
        headers=_csrf_headers(staff_session),
    )
    assert missing_parent.status_code == 404
    assert missing_parent.json()["error"]["code"] == "CATEGORY_NOT_FOUND"

    cat_b = client.post(
        "/api/v1/admin/categories",
        json={"name": "Cat B", "slug": "cat-b", "parent_id": cat_a["id"], "is_active": False},
        headers=_csrf_headers(staff_session),
    ).json()
    cat_c = client.post(
        "/api/v1/admin/categories",
        json={"name": "Cat C", "slug": "cat-c", "parent_id": cat_b["id"]},
        headers=_csrf_headers(staff_session),
    ).json()

    # Admin can also list (including inactive Cat B) and patch
    admin_session = login_as(client, role="admin")
    list_admin_resp = client.get("/api/v1/admin/categories")
    assert list_admin_resp.status_code == 200
    assert {c["slug"] for c in list_admin_resp.json()["items"]} == {"cat-a", "cat-b", "cat-c"}

    # Missing CSRF on PATCH -> 403
    patch_no_csrf = client.patch(
        f"/api/v1/admin/categories/{cat_a['id']}",
        json={"name": "Blocked"},
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert patch_no_csrf.status_code == 403
    assert patch_no_csrf.json()["error"]["code"] == "CSRF_INVALID"

    # Self-cycle (A.parent_id = A) -> 422 CATEGORY_CYCLE_DETECTED
    self_cycle = client.patch(
        f"/api/v1/admin/categories/{cat_a['id']}",
        json={"parent_id": cat_a["id"]},
        headers=_csrf_headers(admin_session),
    )
    assert self_cycle.status_code == 422
    assert self_cycle.json()["error"]["code"] == "CATEGORY_CYCLE_DETECTED"

    # Multi-hop cycle (A -> B -> C; setting A.parent_id = C) -> 422 CATEGORY_CYCLE_DETECTED
    hop_cycle = client.patch(
        f"/api/v1/admin/categories/{cat_a['id']}",
        json={"parent_id": cat_c["id"]},
        headers=_csrf_headers(admin_session),
    )
    assert hop_cycle.status_code == 422
    assert hop_cycle.json()["error"]["code"] == "CATEGORY_CYCLE_DETECTED"

    # Slug conflict on PATCH -> 409 SLUG_CONFLICT
    patch_conflict = client.patch(
        f"/api/v1/admin/categories/{cat_b['id']}",
        json={"slug": "cat-a"},
        headers=_csrf_headers(admin_session),
    )
    assert patch_conflict.status_code == 409
    assert patch_conflict.json()["error"]["code"] == "SLUG_CONFLICT"

    # Valid PATCH updates fields and can clear parent_id to null
    patch_ok = client.patch(
        f"/api/v1/admin/categories/{cat_b['id']}",
        json={"name": "Cat B Renamed", "parent_id": None, "is_active": True},
        headers=_csrf_headers(admin_session),
    )
    assert patch_ok.status_code == 200
    assert patch_ok.json()["name"] == "Cat B Renamed"
    assert patch_ok.json()["parent_id"] is None
    assert patch_ok.json()["is_active"] is True

    # Verify AuditLog rows for create and update
    db_session.expire_all()
    cat_b_audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.resource_type == "category", AuditLog.resource_id == cat_b["id"])
            .order_by(AuditLog.created_at.asc())
        ).all()
    )
    actions = {row.action: row for row in cat_b_audits}
    assert "CATEGORY_CREATED" in actions
    assert actions["CATEGORY_CREATED"].actor_user_id == uuid.UUID(staff_session.user_id)
    assert actions["CATEGORY_CREATED"].before_state is None
    assert actions["CATEGORY_CREATED"].after_state is not None
    assert actions["CATEGORY_CREATED"].after_state["slug"] == "cat-b"

    assert "CATEGORY_UPDATED" in actions
    assert actions["CATEGORY_UPDATED"].actor_user_id == uuid.UUID(admin_session.user_id)
    assert actions["CATEGORY_UPDATED"].before_state is not None
    assert actions["CATEGORY_UPDATED"].before_state["parent_id"] == cat_a["id"]
    assert actions["CATEGORY_UPDATED"].after_state is not None
    assert actions["CATEGORY_UPDATED"].after_state["name"] == "Cat B Renamed"
    assert actions["CATEGORY_UPDATED"].after_state["parent_id"] is None


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://cdn.example.com/image.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,iVBORw0KGgo=",
        "/assets/../secret.png",
        "/assets/%2e%2e/secret.png",
        r"/assets/sub\image.png",
        r"https://cdn.example.com/sub\image.png",
        "https://cdn.example.com/../secret.png",
        "https://user:pass@cdn.example.com/image.png",
        "/assets/",
        "https://cdn.example.com/img.png%0d%0aX-Injected:1",
        "/assets/img%00.png",
        "https://cdn.example.com/%252e%252e/secret.png",
        "/assets/%5csecret.png",
        "https://cdn.example.com:/img.png",
    ],
)
def test_admin_products_rejects_unsafe_image_urls(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
    bad_url: str,
) -> None:
    """POST /admin/products rejects http://, javascript:, data:, traversal, and backslashes."""
    cat = _seed_category(db_session, name="Images Cat", slug="images-cat")
    staff_session = login_as(client, role="staff")

    resp = client.post(
        "/api/v1/admin/products",
        json={
            "category_id": str(cat.id),
            "title": "Unsafe Image Product",
            "slug": "unsafe-img-prod",
            "images": [{"url": bad_url, "alt_text": "bad", "is_primary": True}],
        },
        headers=_csrf_headers(staff_session),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_products_rbac_crud_activation_guard_images_filters_and_archive_audit(
    client: TestClient,
    db_session: Session,
    login_as: Callable[..., AuthenticatedSession],
) -> None:
    """Admin product endpoints cover RBAC, CSRF, image rules, activation guard, filters, archive."""
    cat1 = _seed_category(db_session, name="Tops", slug="tops")
    cat2 = _seed_category(db_session, name="Bottoms", slug="bottoms")

    # Unauthenticated and customer checks
    assert client.get("/api/v1/admin/products").status_code == 401
    cust_session = login_as(client, role="customer")
    assert client.get("/api/v1/admin/products").status_code == 403
    assert (
        client.post(
            "/api/v1/admin/products",
            json={"category_id": str(cat1.id), "title": "Denied", "slug": "denied"},
            headers=_csrf_headers(cust_session),
        ).status_code
        == 403
    )

    staff_session = login_as(client, role="staff")

    # Missing CSRF on POST / PATCH / DELETE -> 403
    assert (
        client.post(
            "/api/v1/admin/products",
            json={"category_id": str(cat1.id), "title": "No CSRF", "slug": "no-csrf"},
            headers={"Origin": DEFAULT_ORIGIN},
        ).status_code
        == 403
    )

    # Multiple primary images rejected -> 422
    multi_primary = client.post(
        "/api/v1/admin/products",
        json={
            "category_id": str(cat1.id),
            "title": "Two Primaries",
            "slug": "two-primaries",
            "images": [
                {"url": "https://cdn.example.com/1.webp", "is_primary": True},
                {"url": "/assets/products/2.webp", "is_primary": True},
            ],
        },
        headers=_csrf_headers(staff_session),
    )
    assert multi_primary.status_code == 422
    assert multi_primary.json()["error"]["code"] == "VALIDATION_ERROR"

    # Creating ACTIVE product without variants rejected -> 422
    active_on_create = client.post(
        "/api/v1/admin/products",
        json={
            "category_id": str(cat1.id),
            "title": "Early Active",
            "slug": "early-active",
            "status": "ACTIVE",
        },
        headers=_csrf_headers(staff_session),
    )
    assert active_on_create.status_code == 422
    assert active_on_create.json()["error"]["code"] == "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT"

    # Missing category -> 404 CATEGORY_NOT_FOUND
    missing_cat = client.post(
        "/api/v1/admin/products",
        json={
            "category_id": str(uuid.uuid4()),
            "title": "No Cat",
            "slug": "no-cat",
        },
        headers=_csrf_headers(staff_session),
    )
    assert missing_cat.status_code == 404
    assert missing_cat.json()["error"]["code"] == "CATEGORY_NOT_FOUND"

    # Valid POST creating DRAFT product with https:// and /assets/ images -> 201
    create_resp = client.post(
        "/api/v1/admin/products",
        json={
            "category_id": str(cat1.id),
            "title": "Merino 100% Crewneck",
            "slug": "merino-crewneck",
            "description": "Ultra-soft knitwear.",
            "images": [
                {
                    "url": "https://cdn.example.com/merino-front.webp",
                    "alt_text": "Front",
                    "sort_order": 0,
                    "is_primary": True,
                },
                {
                    "url": "/assets/products/merino-back.webp",
                    "alt_text": "Back",
                    "sort_order": 1,
                    "is_primary": False,
                },
            ],
        },
        headers=_csrf_headers(staff_session),
    )
    assert create_resp.status_code == 201, create_resp.text
    prod = create_resp.json()
    prod_id = prod["id"]
    assert prod["status"] == "DRAFT"
    assert len(prod["images"]) == 2

    # Duplicate slug on POST -> 409 SLUG_CONFLICT
    dup_slug = client.post(
        "/api/v1/admin/products",
        json={"category_id": str(cat1.id), "title": "Dup", "slug": "merino-crewneck"},
        headers=_csrf_headers(staff_session),
    )
    assert dup_slug.status_code == 409
    assert dup_slug.json()["error"]["code"] == "SLUG_CONFLICT"

    # Admin detail returns DRAFT product; unknown ID returns 404 PRODUCT_NOT_FOUND
    detail_draft = client.get(f"/api/v1/admin/products/{prod_id}")
    assert detail_draft.status_code == 200
    assert detail_draft.json()["status"] == "DRAFT"
    missing_detail = client.get(f"/api/v1/admin/products/{uuid.uuid4()}")
    assert missing_detail.status_code == 404
    assert missing_detail.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    # PATCH to ACTIVE with 0 variants -> 422 PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT
    activate_no_var = client.patch(
        f"/api/v1/admin/products/{prod_id}",
        json={"status": "ACTIVE"},
        headers=_csrf_headers(staff_session),
    )
    assert activate_no_var.status_code == 422
    assert activate_no_var.json()["error"]["code"] == "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT"

    # Add an inactive variant only -> still 422 on activation
    inactive_var = ProductVariant(
        product_id=uuid.UUID(prod_id),
        sku="MRN_INACTIVE",
        title="Inactive",
        attributes_json={},
        price_cents=4000,
        currency="USD",
        is_active=False,
    )
    db_session.add(inactive_var)
    db_session.commit()
    activate_inactive_var = client.patch(
        f"/api/v1/admin/products/{prod_id}",
        json={"status": "ACTIVE"},
        headers=_csrf_headers(staff_session),
    )
    assert activate_inactive_var.status_code == 422
    assert (
        activate_inactive_var.json()["error"]["code"] == "PRODUCT_CANNOT_ACTIVATE_WITHOUT_VARIANT"
    )

    # Seed an active variant via admin endpoint and activate + replace primary image
    add_var = client.post(
        f"/api/v1/admin/products/{prod_id}/variants",
        json={
            "sku": "MRN_ACT-01",
            "title": "Charcoal / M",
            "price_cents": 5000,
            "initial_on_hand": 12,
        },
        headers=_csrf_headers(staff_session),
    )
    assert add_var.status_code == 201

    # Seed second product in cat2 for admin filter + slug conflict checks
    other_prod, _ = _seed_product(
        db_session,
        category_id=cat2.id,
        title="Linen Pant 1000",
        slug="linen-pant",
        status="DRAFT",
        variants=[{"sku": "LIN-01", "price_cents": 7000, "on_hand": 5}],
    )

    patch_conflict = client.patch(
        f"/api/v1/admin/products/{prod_id}",
        json={"slug": other_prod.slug},
        headers=_csrf_headers(staff_session),
    )
    assert patch_conflict.status_code == 409
    assert patch_conflict.json()["error"]["code"] == "SLUG_CONFLICT"

    patch_bad_cat = client.patch(
        f"/api/v1/admin/products/{prod_id}",
        json={"category_id": str(uuid.uuid4())},
        headers=_csrf_headers(staff_session),
    )
    assert patch_bad_cat.status_code == 404
    assert patch_bad_cat.json()["error"]["code"] == "CATEGORY_NOT_FOUND"

    # Valid PATCH: activate product & replace primary image cleanly
    activate_ok = client.patch(
        f"/api/v1/admin/products/{prod_id}",
        json={
            "status": "ACTIVE",
            "images": [
                {
                    "url": "/assets/products/merino-new-primary.webp",
                    "alt_text": "New Primary",
                    "sort_order": 0,
                    "is_primary": True,
                }
            ],
        },
        headers=_csrf_headers(staff_session),
    )
    assert activate_ok.status_code == 200, activate_ok.text
    assert activate_ok.json()["status"] == "ACTIVE"
    assert len(activate_ok.json()["images"]) == 1
    assert activate_ok.json()["images"][0]["url"] == "/assets/products/merino-new-primary.webp"

    # Admin list returns DRAFT, ACTIVE, ARCHIVED with q escaped, status, and category_id filters
    list_all = client.get("/api/v1/admin/products")
    assert list_all.status_code == 200
    assert list_all.json()["total"] == 2

    list_pct = client.get("/api/v1/admin/products", params={"q": "100%"})
    assert [p["slug"] for p in list_pct.json()["items"]] == ["merino-crewneck"]

    list_cat2 = client.get("/api/v1/admin/products", params={"category_id": str(cat2.id)})
    assert [p["slug"] for p in list_cat2.json()["items"]] == ["linen-pant"]

    list_active = client.get("/api/v1/admin/products", params={"status": "ACTIVE"})
    assert [p["slug"] for p in list_active.json()["items"]] == ["merino-crewneck"]

    # DELETE soft-archives to ARCHIVED (200 OK), hidden on storefront, visible in admin
    admin_session = login_as(client, role="admin")
    del_no_csrf = client.delete(
        f"/api/v1/admin/products/{prod_id}",
        headers={"Origin": DEFAULT_ORIGIN},
    )
    assert del_no_csrf.status_code == 403

    archive_resp = client.delete(
        f"/api/v1/admin/products/{prod_id}",
        headers=_csrf_headers(admin_session),
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["status"] == "ARCHIVED"

    assert client.get("/api/v1/products/merino-crewneck").status_code == 404
    assert client.get(f"/api/v1/admin/products/{prod_id}").json()["status"] == "ARCHIVED"
    assert [
        p["slug"]
        for p in client.get("/api/v1/admin/products", params={"status": "ARCHIVED"}).json()["items"]
    ] == ["merino-crewneck"]

    # Verify AuditLog entries (PRODUCT_CREATED, PRODUCT_UPDATED, PRODUCT_ARCHIVED)
    db_session.expire_all()
    audits = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.resource_type == "product", AuditLog.resource_id == prod_id)
            .order_by(AuditLog.created_at.asc())
        ).all()
    )
    by_action = {row.action: row for row in audits}
    assert set(by_action.keys()) >= {"PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_ARCHIVED"}
    assert by_action["PRODUCT_CREATED"].before_state is None
    assert by_action["PRODUCT_CREATED"].after_state is not None
    assert by_action["PRODUCT_CREATED"].after_state["status"] == "DRAFT"

    archived_log = by_action["PRODUCT_ARCHIVED"]
    assert archived_log.actor_user_id == uuid.UUID(admin_session.user_id)
    assert archived_log.before_state is not None
    assert archived_log.before_state["status"] == "ACTIVE"
    assert archived_log.after_state is not None
    assert archived_log.after_state["status"] == "ARCHIVED"


def test_l03_triple_encoded_image_url_rejected_and_non_slug_integrity_conflict(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L-03: Triple-encoded traversal rejected; non-slug IntegrityError -> INTEGRITY_CONFLICT."""
    with pytest.raises(ValueError, match="path traversal"):
        validate_product_image_url("/assets/%25252e%25252e/secret.png")

    def _raise_fk_integrity() -> None:
        raise IntegrityError(
            "INSERT INTO categories ...",
            {},
            Exception("FOREIGN KEY constraint failed: fk_categories_parent_id_categories"),
        )

    monkeypatch.setattr(db_session, "flush", _raise_fk_integrity)
    with pytest.raises(ConflictError) as exc_info:
        create_category(
            db_session,
            payload=CreateCategoryRequest(name="Integrity Test", slug="integrity-test"),
            actor_user_id=uuid.uuid4(),
            ip_address=None,
        )
    assert exc_info.value.code == "INTEGRITY_CONFLICT"
