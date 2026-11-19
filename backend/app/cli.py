"""Operational CLI for user bootstrap and non-production Atelier catalog seeding."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import record_audit_log
from app.core.config import Settings, get_settings
from app.core.database import get_session_factory
from app.core.security import hash_password
from app.models.catalog import Category, Product, ProductImage
from app.models.identity import User
from app.models.inventory import InventoryLedgerEntry, InventoryStock, ProductVariant
from app.modules.catalog.schemas import strip_single_line
from app.modules.identity.dependencies import revoke_all_user_sessions
from app.modules.identity.schemas import UserRole

_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_VALID_ROLES: tuple[UserRole, ...] = ("customer", "staff", "admin")


@dataclass(frozen=True, slots=True)
class _DemoCategorySeed:
    slug: str
    name: str
    description: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class _DemoVariantSeed:
    sku: str
    title: str
    attributes: dict[str, str]
    price_cents: int
    compare_at_price_cents: int | None
    initial_on_hand: int
    low_stock_threshold: int


@dataclass(frozen=True, slots=True)
class _DemoProductSeed:
    slug: str
    category_slug: str
    title: str
    description: str
    alt_text: str
    variants: tuple[_DemoVariantSeed, ...]


_DEMO_CATEGORIES: tuple[_DemoCategorySeed, ...] = (
    _DemoCategorySeed(
        slug="apparel",
        name="Apparel",
        description="Tailored natural-fiber garments crafted for daily movement.",
        sort_order=1,
    ),
    _DemoCategorySeed(
        slug="accessories",
        name="Accessories",
        description="Full-grain leather goods and structured travel carryalls.",
        sort_order=2,
    ),
    _DemoCategorySeed(
        slug="home",
        name="Home",
        description="Hand-finished ceramics, solid timberware, and woven textiles.",
        sort_order=3,
    ),
)

_DEMO_PRODUCTS: tuple[_DemoProductSeed, ...] = (
    _DemoProductSeed(
        slug="linen-overshirt",
        category_slug="apparel",
        title="Atelier Linen Overshirt",
        description=(
            "Midweight washed Belgian linen overshirt with corozo buttons and twin utility pockets."
        ),
        alt_text="Atelier Linen Overshirt in undyed sand",
        variants=(
            _DemoVariantSeed(
                sku="ATL-LINEN-SND-M",
                title="Sand / Medium",
                attributes={"color": "Sand", "size": "M"},
                price_cents=14500,
                compare_at_price_cents=16500,
                initial_on_hand=24,
                low_stock_threshold=5,
            ),
            _DemoVariantSeed(
                sku="ATL-LINEN-SND-L",
                title="Sand / Large",
                attributes={"color": "Sand", "size": "L"},
                price_cents=14500,
                compare_at_price_cents=16500,
                initial_on_hand=3,
                low_stock_threshold=5,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="merino-crewneck",
        category_slug="apparel",
        title="Fine Merino Crewneck",
        description=(
            "Extra-fine 19.5-micron merino knitwear spun for breathable year-round layering."
        ),
        alt_text="Fine Merino Crewneck in deep pine",
        variants=(
            _DemoVariantSeed(
                sku="ATL-MERINO-PIN-M",
                title="Atelier Pine / Medium",
                attributes={"color": "Atelier Pine", "size": "M"},
                price_cents=12000,
                compare_at_price_cents=None,
                initial_on_hand=18,
                low_stock_threshold=5,
            ),
            _DemoVariantSeed(
                sku="ATL-MERINO-OBS-L",
                title="Obsidian / Large",
                attributes={"color": "Obsidian", "size": "L"},
                price_cents=12000,
                compare_at_price_cents=None,
                initial_on_hand=15,
                low_stock_threshold=5,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="leather-weekender",
        category_slug="accessories",
        title="Bridle Leather Weekender",
        description=(
            "Vegetable-tanned bridle leather duffle with solid brass hardware and cotton lining."
        ),
        alt_text="Bridle Leather Weekender bag in espresso brown",
        variants=(
            _DemoVariantSeed(
                sku="ATL-WKND-ESP-OS",
                title="Espresso / One Size",
                attributes={"color": "Espresso", "capacity": "42L"},
                price_cents=32000,
                compare_at_price_cents=36000,
                initial_on_hand=10,
                low_stock_threshold=3,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="canvas-tote",
        category_slug="accessories",
        title="Waxed Canvas Studio Tote",
        description=(
            "18oz water-repellent waxed canvas carryall with reinforced bridle leather handles."
        ),
        alt_text="Waxed Canvas Studio Tote in stone",
        variants=(
            _DemoVariantSeed(
                sku="ATL-TOTE-STN-OS",
                title="Stone / Studio",
                attributes={"color": "Stone", "size": "Standard"},
                price_cents=9500,
                compare_at_price_cents=None,
                initial_on_hand=32,
                low_stock_threshold=5,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="stoneware-mug-set",
        category_slug="home",
        title="Kiln Stoneware Mug Set",
        description=(
            "Set of four wheel-thrown speckled stoneware mugs finished in satin ash glaze."
        ),
        alt_text="Set of four Kiln Stoneware Mugs",
        variants=(
            _DemoVariantSeed(
                sku="ATL-MUG-ASH-SET4",
                title="Ash Glaze / Set of 4",
                attributes={"finish": "Ash Glaze", "pack": "4"},
                price_cents=5400,
                compare_at_price_cents=None,
                initial_on_hand=28,
                low_stock_threshold=5,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="walnut-serving-board",
        category_slug="home",
        title="Oiled Walnut Serving Board",
        description=(
            "Single-slab American black walnut serving board finished with food-safe beeswax oil."
        ),
        alt_text="Oiled Walnut Serving Board",
        variants=(
            _DemoVariantSeed(
                sku="ATL-BRD-WLN-LG",
                title="Black Walnut / Large",
                attributes={"material": "Walnut", "length": "45cm"},
                price_cents=7800,
                compare_at_price_cents=None,
                initial_on_hand=16,
                low_stock_threshold=4,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="ceramic-table-lamp",
        category_slug="home",
        title="Matte Ceramic Table Lamp",
        description=(
            "Sculptural stoneware base paired with a natural linen drum shade and brass dimmer."
        ),
        alt_text="Matte Ceramic Table Lamp in warm alabaster",
        variants=(
            _DemoVariantSeed(
                sku="ATL-LMP-ALB-OS",
                title="Warm Alabaster / Standard",
                attributes={"finish": "Warm Alabaster", "voltage": "120V"},
                price_cents=21000,
                compare_at_price_cents=None,
                initial_on_hand=2,
                low_stock_threshold=5,
            ),
        ),
    ),
    _DemoProductSeed(
        slug="wool-throw-blanket",
        category_slug="home",
        title="Herringbone Wool Throw Blanket",
        description=(
            "Loomed pure lambswool throw in subtle herringbone weave with hand-twisted fringe."
        ),
        alt_text="Herringbone Wool Throw Blanket in oat and charcoal",
        variants=(
            _DemoVariantSeed(
                sku="ATL-THRW-OAT-OS",
                title="Oat & Charcoal / 140x200cm",
                attributes={"color": "Oat / Charcoal", "dimensions": "140x200cm"},
                price_cents=16500,
                compare_at_price_cents=None,
                initial_on_hand=20,
                low_stock_threshold=5,
            ),
            _DemoVariantSeed(
                sku="ATL-THRW-MOS-OS",
                title="Moss / 140x200cm",
                attributes={"color": "Moss", "dimensions": "140x200cm"},
                price_cents=16500,
                compare_at_price_cents=None,
                initial_on_hand=14,
                low_stock_threshold=5,
            ),
        ),
    ),
)

_DEMO_USERS: tuple[tuple[str, str, UserRole, str, str], ...] = (
    (
        "admin@atelier.example",
        "Atelier Admin",
        "admin",
        "DEMO_ADMIN_PASSWORD",
        "AtelierAdmin!2026",
    ),
    (
        "staff@atelier.example",
        "Atelier Staff",
        "staff",
        "DEMO_STAFF_PASSWORD",
        "AtelierStaff!2026",
    ),
    (
        "customer@atelier.example",
        "Atelier Customer",
        "customer",
        "DEMO_CUSTOMER_PASSWORD",
        "AtelierCustomer!2026",
    ),
)


def _validate_email(raw_email: str) -> str:
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_email):
        raise ValueError("Email must not contain control characters.")
    cleaned = raw_email.strip()
    validated = str(_EMAIL_ADAPTER.validate_python(cleaned)).strip().lower()
    if len(validated) > 320:
        raise ValueError("Email exceeds maximum length of 320 characters.")
    return validated


def _validate_full_name(raw_full_name: str) -> str:
    cleaned = strip_single_line(raw_full_name, field_name="Full name")
    if not isinstance(cleaned, str) or not cleaned or len(cleaned) > 120:
        raise ValueError("Full name must be between 1 and 120 characters.")
    return cleaned


def _validate_password(raw_password: str) -> str:
    if "\x00" in raw_password:
        raise ValueError("Password must not contain NUL characters.")
    if len(raw_password) < 10 or len(raw_password) > 128:
        raise ValueError("Password must be between 10 and 128 characters.")
    return raw_password


def _resolve_cli_password(
    *,
    password_arg: str | None,
    password_env_arg: str | None,
) -> str:
    if password_arg is not None and password_env_arg is not None:
        raise ValueError("Specify either --password or --password-env, not both.")
    if password_arg is None and password_env_arg is None:
        raise ValueError("One of --password-env or --password is required.")

    if password_env_arg is not None:
        env_name = password_env_arg.strip()
        if not env_name:
            raise ValueError("--password-env must specify a non-empty environment variable name.")
        env_value = os.environ.get(env_name)
        if env_value is None or not env_value:
            raise ValueError(f"Environment variable '{env_name}' is unset or empty.")
        return _validate_password(env_value)

    assert password_arg is not None
    return _validate_password(password_arg)


def _cmd_create_user(
    args: argparse.Namespace,
    *,
    session_factory: sessionmaker[Session] | None,
    settings: Settings | None,
) -> int:
    try:
        canonical_email = _validate_email(args.email)
        full_name = _validate_full_name(args.full_name)
        password = _resolve_cli_password(
            password_arg=args.password,
            password_env_arg=args.password_env,
        )
    except (ValueError, ValidationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    role: UserRole = args.role
    if role not in _VALID_ROLES:
        print(f"Error: Invalid role '{role}'.", file=sys.stderr)
        return 1

    active_settings = settings or get_settings()
    factory = session_factory or get_session_factory(active_settings)
    pw_hash = hash_password(password)

    with factory() as db:
        existing = db.scalar(
            select(User).where(func.lower(User.email) == canonical_email)
        )
        if existing is not None:
            if not args.update_if_exists:
                print(
                    f"Error: User '{canonical_email}' already exists. "
                    "Pass --update-if-exists to update.",
                    file=sys.stderr,
                )
                return 1

            before_state: dict[str, Any] = {
                "id": str(existing.id),
                "email": existing.email,
                "full_name": existing.full_name,
                "role": existing.role,
                "is_active": existing.is_active,
            }
            existing.full_name = full_name
            existing.role = role
            existing.password_hash = pw_hash
            existing.is_active = True
            revoked = revoke_all_user_sessions(db, existing.id)
            record_audit_log(
                db,
                actor_user_id=None,
                action="CLI_USER_UPDATED",
                resource_type="user",
                resource_id=str(existing.id),
                before_state=before_state,
                after_state={
                    "id": str(existing.id),
                    "email": existing.email,
                    "full_name": existing.full_name,
                    "role": existing.role,
                    "is_active": existing.is_active,
                    "revoked_sessions_count": revoked,
                },
            )
            db.commit()
            print(
                f"Updated user {existing.email} (id={existing.id}, role={existing.role})"
            )
            return 0

        user = User(
            email=canonical_email,
            password_hash=pw_hash,
            full_name=full_name,
            role=role,
            is_active=True,
        )
        db.add(user)
        db.flush()
        record_audit_log(
            db,
            actor_user_id=None,
            action="CLI_USER_CREATED",
            resource_type="user",
            resource_id=str(user.id),
            before_state=None,
            after_state={
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role,
                "is_active": user.is_active,
            },
        )
        db.commit()
        print(f"Created user {user.email} (id={user.id}, role={user.role})")
        return 0


def _upsert_demo_primary_image(
    db: Session,
    *,
    product: Product,
    url: str,
    alt_text: str,
) -> None:
    images = list(
        db.scalars(
            select(ProductImage)
            .where(ProductImage.product_id == product.id)
            .order_by(ProductImage.sort_order.asc(), ProductImage.id.asc())
        ).all()
    )
    matching = next((img for img in images if img.url == url), None)
    for img in images:
        if img is not matching and img.is_primary:
            img.is_primary = False
    if images:
        db.flush()

    if matching is not None:
        matching.alt_text = alt_text
        matching.sort_order = 0
        matching.is_primary = True
        return

    db.add(
        ProductImage(
            product_id=product.id,
            url=url,
            alt_text=alt_text,
            sort_order=0,
            is_primary=True,
        )
    )


def _cmd_seed_demo(
    args: argparse.Namespace,
    *,
    session_factory: sessionmaker[Session] | None,
    settings: Settings | None,
) -> int:
    active_settings = settings or get_settings()
    if active_settings.environment == "production":
        print(
            "Error: Refusing to run seed-demo when ENVIRONMENT=production.",
            file=sys.stderr,
        )
        return 1

    factory = session_factory or get_session_factory(active_settings)
    created_products = 0
    created_variants = 0
    created_users = 0

    with factory() as db:
        categories_by_slug: dict[str, Category] = {}
        for cat_seed in _DEMO_CATEGORIES:
            category = db.scalar(select(Category).where(Category.slug == cat_seed.slug))
            if category is None:
                category = Category(
                    name=cat_seed.name,
                    slug=cat_seed.slug,
                    description=cat_seed.description,
                    is_active=True,
                    sort_order=cat_seed.sort_order,
                )
                db.add(category)
                db.flush()
            else:
                category.name = cat_seed.name
                category.description = cat_seed.description
                category.is_active = True
                category.sort_order = cat_seed.sort_order
            categories_by_slug[cat_seed.slug] = category

        for prod_seed in _DEMO_PRODUCTS:
            category = categories_by_slug[prod_seed.category_slug]
            product = db.scalar(select(Product).where(Product.slug == prod_seed.slug))
            if product is None:
                product = Product(
                    category_id=category.id,
                    title=prod_seed.title,
                    slug=prod_seed.slug,
                    description=prod_seed.description,
                    status="ACTIVE",
                )
                db.add(product)
                db.flush()
                created_products += 1
            else:
                product.category_id = category.id
                product.title = prod_seed.title
                product.description = prod_seed.description
                product.status = "ACTIVE"

            _upsert_demo_primary_image(
                db,
                product=product,
                url=f"/assets/products/{prod_seed.slug}.png",
                alt_text=prod_seed.alt_text,
            )

            for var_seed in prod_seed.variants:
                variant = db.scalar(
                    select(ProductVariant).where(ProductVariant.sku == var_seed.sku)
                )
                if variant is None:
                    variant = ProductVariant(
                        product_id=product.id,
                        sku=var_seed.sku,
                        title=var_seed.title,
                        attributes_json=dict(var_seed.attributes),
                        price_cents=var_seed.price_cents,
                        compare_at_price_cents=var_seed.compare_at_price_cents,
                        currency="USD",
                        is_active=True,
                    )
                    db.add(variant)
                    db.flush()
                    created_variants += 1
                else:
                    variant.product_id = product.id
                    variant.title = var_seed.title
                    variant.attributes_json = dict(var_seed.attributes)
                    variant.price_cents = var_seed.price_cents
                    variant.compare_at_price_cents = var_seed.compare_at_price_cents
                    variant.currency = "USD"
                    variant.is_active = True

                stock = db.scalar(
                    select(InventoryStock).where(InventoryStock.variant_id == variant.id)
                )
                if stock is None:
                    db.add(
                        InventoryStock(
                            variant_id=variant.id,
                            on_hand=var_seed.initial_on_hand,
                            reserved=0,
                            low_stock_threshold=var_seed.low_stock_threshold,
                            version=1,
                        )
                    )
                    db.add(
                        InventoryLedgerEntry(
                            variant_id=variant.id,
                            order_id=None,
                            actor_user_id=None,
                            reason="INITIAL_SEED",
                            delta_on_hand=var_seed.initial_on_hand,
                            delta_reserved=0,
                            on_hand_after=var_seed.initial_on_hand,
                            reserved_after=0,
                            note="Demo catalog initial seed",
                        )
                    )

        if args.with_demo_users:
            for email, full_name, role, env_var, default_pw in _DEMO_USERS:
                raw_pw = os.environ.get(env_var) or default_pw
                try:
                    validated_pw = _validate_password(raw_pw)
                except ValueError as exc:
                    db.rollback()
                    print(f"Error ({env_var}): {exc}", file=sys.stderr)
                    return 1

                existing_user = db.scalar(
                    select(User).where(func.lower(User.email) == email)
                )
                if existing_user is None:
                    db.add(
                        User(
                            email=email,
                            password_hash=hash_password(validated_pw),
                            full_name=full_name,
                            role=role,
                            is_active=True,
                        )
                    )
                    created_users += 1

        db.commit()

    print(
        f"Seeded demo catalog (categories={len(_DEMO_CATEGORIES)}, "
        f"products={len(_DEMO_PRODUCTS)}, created_products={created_products}, "
        f"created_variants={created_variants}, created_demo_users={created_users})"
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="E-commerce backend administration and non-production seeding CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_user_parser = subparsers.add_parser(
        "create-user",
        help="Create or update a user account (suitable for bootstrapping initial admin).",
    )
    create_user_parser.add_argument("--email", required=True, help="User email address.")
    create_user_parser.add_argument("--full-name", required=True, help="User full name.")
    create_user_parser.add_argument(
        "--role",
        choices=_VALID_ROLES,
        default="customer",
        help="Account role (admin | staff | customer).",
    )
    create_user_parser.add_argument(
        "--password-env",
        default=None,
        help="Environment variable name holding the password (recommended).",
    )
    create_user_parser.add_argument(
        "--password",
        default=None,
        help="Plaintext password (10..128 chars; prefer --password-env).",
    )
    create_user_parser.add_argument(
        "--update-if-exists",
        action="store_true",
        default=False,
        help="Update full_name, role, password, and active status if email already exists.",
    )

    seed_demo_parser = subparsers.add_parser(
        "seed-demo",
        help="Idempotently seed non-production Atelier demo catalog (refuses production).",
    )
    seed_demo_parser.add_argument(
        "--with-demo-users",
        action="store_true",
        default=False,
        help="Also create demo admin/staff/customer accounts in non-production environments.",
    )

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    settings: Settings | None = None,
) -> int:
    """Execute CLI subcommands and return process exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "create-user":
        return _cmd_create_user(args, session_factory=session_factory, settings=settings)
    if args.command == "seed-demo":
        return _cmd_seed_demo(args, session_factory=session_factory, settings=settings)

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
