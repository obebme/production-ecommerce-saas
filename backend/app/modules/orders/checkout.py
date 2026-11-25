"""Idempotent checkout orchestration with server-authoritative totals and stock reservation."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, RateLimitExceededError, ValidationAppError
from app.core.observability import checkout_events_total
from app.core.order_events import append_order_event
from app.core.security import GuestPendingOrderTracker, hash_token, verify_guest_order_token
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Product
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderItem
from app.models.payment import PaymentIntent
from app.modules.catalog.schemas import ensure_utc_datetime
from app.modules.identity.dependencies import AuthContext
from app.modules.inventory.service import reclaim_expired_reservations_for_variants
from app.modules.orders.common import (
    _INT32_MAX,
    ADVISORY_LOCK_NS_CHECKOUT,
    ADVISORY_LOCK_NS_GUEST_PENDING_CAP,
    GUEST_ACCESS_TTL,
    MAX_ACTIVE_GUEST_PENDING_ORDERS,
    RESERVATION_TTL,
    _acquire_advisory_xact_lock,
    _build_checkout_response_dto,
    _compute_checkout_fingerprint,
    _generate_order_number,
    _load_order_items,
    _sqlite_guest_cap_guard,
    compute_payment_intent_fingerprint,
    parse_guest_orders_cookie,
)
from app.modules.orders.schemas import CheckoutOrderResponseDTO, CreateCheckoutOrderRequest
from app.modules.payments.provider import get_default_payment_registry


def _find_actor_active_cart(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    guest_token_hash: str | None,
    now: datetime,
) -> Cart | None:
    if user_id is not None:
        cart = db.scalar(
            select(Cart).where(Cart.user_id == user_id, Cart.status == "ACTIVE")
        )
    elif guest_token_hash is not None:
        cart = db.scalar(
            select(Cart).where(
                Cart.guest_token_hash == guest_token_hash,
                Cart.status == "ACTIVE",
            )
        )
    else:
        return None

    if cart is not None and ensure_utc_datetime(cart.expires_at) <= now:
        return None
    return cart


def _find_matching_guest_pending_order_ids(
    db: Session,
    *,
    guest_client_key_hash: str,
    guest_orders_cookie: str | None,
    settings: Settings,
    now: datetime,
) -> list[uuid.UUID]:
    """Return PENDING_PAYMENT guest order IDs matching client IP hash or valid cookie tokens."""
    cookie_token_map = dict(parse_guest_orders_cookie(guest_orders_cookie))
    cookie_order_numbers = list(cookie_token_map.keys())

    match_clause = (
        or_(
            Order.guest_client_key_hash == guest_client_key_hash,
            Order.order_number.in_(cookie_order_numbers),
        )
        if cookie_order_numbers
        else (Order.guest_client_key_hash == guest_client_key_hash)
    )

    candidate_rows = db.execute(
        select(
            Order.id,
            Order.order_number,
            Order.guest_token_version,
            Order.guest_access_expires_at,
            Order.guest_client_key_hash,
        )
        .where(
            Order.user_id.is_(None),
            Order.status == "PENDING_PAYMENT",
            match_clause,
        )
        .execution_options(populate_existing=True)
    ).all()

    candidate_order_ids: list[uuid.UUID] = []
    for oid, order_num, guest_ver, guest_exp, row_client_hash in candidate_rows:
        if row_client_hash == guest_client_key_hash:
            candidate_order_ids.append(oid)
            continue
        cookie_token = cookie_token_map.get(order_num)
        if (
            cookie_token is not None
            and guest_exp is not None
            and ensure_utc_datetime(guest_exp) > now
            and verify_guest_order_token(
                cookie_token,
                oid,
                order_num,
                guest_ver,
                settings.guest_order_hmac_secret,
            )
        ):
            candidate_order_ids.append(oid)
    return candidate_order_ids


def _reclaim_expired_guest_pending_orders(
    db: Session,
    *,
    guest_client_key_hash: str,
    guest_orders_cookie: str | None,
    settings: Settings,
    now: datetime,
) -> None:
    """Lazily reclaim expired reservations for matching guest PENDING_PAYMENT orders pre-lock."""
    candidate_order_ids = _find_matching_guest_pending_order_ids(
        db,
        guest_client_key_hash=guest_client_key_hash,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        now=now,
    )
    if not candidate_order_ids:
        return

    res_rows = db.execute(
        select(
            InventoryReservation.variant_id,
            InventoryReservation.expires_at,
        ).where(
            InventoryReservation.order_id.in_(candidate_order_ids),
            InventoryReservation.status == "ACTIVE",
        )
    ).all()
    expired_variants = [
        vid for vid, exp in res_rows if ensure_utc_datetime(exp) < now
    ]
    if expired_variants and reclaim_expired_reservations_for_variants(
        db, expired_variants, now=now
    ) > 0:
        db.commit()


def _enforce_guest_pending_order_cap_tx(
    db: Session,
    tracker: GuestPendingOrderTracker,
    *,
    client_ip_key: str,
    guest_client_key_hash: str,
    guest_orders_cookie: str | None,
    settings: Settings,
) -> None:
    """Enforce DB-backed MAX_ACTIVE_GUEST_PENDING_ORDERS inside the locked checkout TX."""
    now = datetime.now(UTC)
    candidate_order_ids = _find_matching_guest_pending_order_ids(
        db,
        guest_client_key_hash=guest_client_key_hash,
        guest_orders_cookie=guest_orders_cookie,
        settings=settings,
        now=now,
    )
    if not candidate_order_ids:
        tracker.retain_active(client_ip_key, set())
        return

    res_rows = db.execute(
        select(
            InventoryReservation.order_id,
            InventoryReservation.expires_at,
        )
        .join(Order, Order.id == InventoryReservation.order_id)
        .where(
            Order.id.in_(candidate_order_ids),
            Order.status == "PENDING_PAYMENT",
            InventoryReservation.status == "ACTIVE",
        )
    ).all()

    active_pending_ids = {
        oid for oid, exp in res_rows if ensure_utc_datetime(exp) >= now
    }
    tracker.retain_active(client_ip_key, active_pending_ids)
    if len(active_pending_ids) >= MAX_ACTIVE_GUEST_PENDING_ORDERS:
        db.rollback()
        raise RateLimitExceededError(
            code="RATE_LIMIT_EXCEEDED",
            message=(
                "Maximum of 3 active pending guest orders reached. "
                "Complete or cancel an existing pending order."
            ),
            details={"max_active_pending_orders": MAX_ACTIVE_GUEST_PENDING_ORDERS},
        )


def create_checkout_order(
    db: Session,
    *,
    auth: AuthContext | None,
    raw_guest_cart_token: str | None,
    guest_orders_cookie: str | None,
    idempotency_key: str,
    payload: CreateCheckoutOrderRequest,
    settings: Settings,
    tracker: GuestPendingOrderTracker,
    client_ip_key: str,
) -> tuple[CheckoutOrderResponseDTO, bool]:
    """Convert active cart to PENDING_PAYMENT order idempotently with stock reservation.

    Returns (CheckoutOrderResponseDTO, is_replay).
    """
    actor_type = "user" if auth is not None else "guest"
    try:
        now = datetime.now(UTC)
        guest_token_hash: str | None = None
        guest_client_key_hash: str | None = None
        if auth is not None:
            actor_scope = f"user:{auth.user.id}"
        else:
            if not raw_guest_cart_token:
                raise ConflictError(
                    code="CART_EMPTY",
                    message="Your cart is empty.",
                )
            guest_token_hash = hash_token(raw_guest_cart_token)
            guest_client_key_hash = hash_token(f"guest_ip:{client_ip_key}")
            actor_scope = f"guest:{guest_token_hash}"

        scoped_key = hash_token(f"{actor_scope}:{idempotency_key}")

        # Pre-step: lazily reclaim expired reservations for cart items and matching guest
        # pending orders before acquiring checkout transaction locks.
        pre_cart = _find_actor_active_cart(
            db,
            user_id=auth.user.id if auth is not None else None,
            guest_token_hash=guest_token_hash,
            now=now,
        )
        if pre_cart is not None:
            pre_variant_ids = list(
                db.scalars(
                    select(CartItem.variant_id).where(CartItem.cart_id == pre_cart.id)
                ).all()
            )
            if pre_variant_ids and reclaim_expired_reservations_for_variants(
                db, pre_variant_ids, now=now
            ) > 0:
                db.commit()

        if guest_client_key_hash is not None:
            _reclaim_expired_guest_pending_orders(
                db,
                guest_client_key_hash=guest_client_key_hash,
                guest_orders_cookie=guest_orders_cookie,
                settings=settings,
                now=datetime.now(UTC),
            )

        with _sqlite_guest_cap_guard(db, guest_client_key_hash):
            dto, is_replay = _create_checkout_order_tx(
                db,
                auth=auth,
                guest_token_hash=guest_token_hash,
                guest_client_key_hash=guest_client_key_hash,
                guest_orders_cookie=guest_orders_cookie,
                scoped_key=scoped_key,
                payload=payload,
                settings=settings,
                tracker=tracker,
                client_ip_key=client_ip_key,
            )
        checkout_events_total.labels(
            outcome="replay" if is_replay else "created",
            actor_type=actor_type,
        ).inc()
        return dto, is_replay
    except Exception:
        checkout_events_total.labels(outcome="error", actor_type=actor_type).inc()
        raise


def _create_checkout_order_tx(
    db: Session,
    *,
    auth: AuthContext | None,
    guest_token_hash: str | None,
    guest_client_key_hash: str | None,
    guest_orders_cookie: str | None,
    scoped_key: str,
    payload: CreateCheckoutOrderRequest,
    settings: Settings,
    tracker: GuestPendingOrderTracker,
    client_ip_key: str,
) -> tuple[CheckoutOrderResponseDTO, bool]:
    payment_adapter = get_default_payment_registry().get(settings.payment_provider)
    shipping_dict = payload.shipping_address.model_dump()
    billing_dict = payload.effective_billing_address().model_dump()

    if guest_client_key_hash is not None:
        _acquire_advisory_xact_lock(
            db,
            ADVISORY_LOCK_NS_GUEST_PENDING_CAP,
            guest_client_key_hash,
        )
    _acquire_advisory_xact_lock(db, ADVISORY_LOCK_NS_CHECKOUT, scoped_key)

    existing_order = db.scalar(
        select(Order)
        .where(Order.idempotency_key == scoped_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    active_cart = _find_actor_active_cart(
        db,
        user_id=auth.user.id if auth is not None else None,
        guest_token_hash=guest_token_hash,
        now=datetime.now(UTC),
    )

    if existing_order is not None:
        return (
            _handle_existing_checkout_idempotency(
                db,
                existing_order=existing_order,
                active_cart=active_cart,
                customer_email=payload.customer_email,
                shipping_dict=shipping_dict,
                billing_dict=billing_dict,
                settings=settings,
            ),
            True,
        )

    if guest_client_key_hash is not None:
        _enforce_guest_pending_order_cap_tx(
            db,
            tracker,
            client_ip_key=client_ip_key,
            guest_client_key_hash=guest_client_key_hash,
            guest_orders_cookie=guest_orders_cookie,
            settings=settings,
        )

    if active_cart is None:
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    locked_cart = db.scalar(
        select(Cart)
        .where(Cart.id == active_cart.id, Cart.status == "ACTIVE")
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_cart is None or ensure_utc_datetime(locked_cart.expires_at) <= datetime.now(UTC):
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    cart_items = list(
        db.scalars(
            select(CartItem)
            .where(CartItem.cart_id == locked_cart.id)
            .order_by(CartItem.variant_id.asc())
        ).all()
    )
    if not cart_items:
        db.rollback()
        raise ConflictError(
            code="CART_EMPTY",
            message="Your cart is empty.",
        )

    variant_ids = sorted({item.variant_id for item in cart_items})

    # Canonical lock order: InventoryStock rows locked in ascending variant_id order.
    locked_stocks = list(
        db.scalars(
            select(InventoryStock)
            .where(InventoryStock.variant_id.in_(variant_ids))
            .order_by(InventoryStock.variant_id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    stocks_by_variant = {stock.variant_id: stock for stock in locked_stocks}

    variant_rows = db.execute(
        select(ProductVariant, Product, Category)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Category, Category.id == Product.category_id)
        .where(ProductVariant.id.in_(variant_ids))
        .order_by(ProductVariant.id.asc())
    ).all()
    catalog_by_variant = {
        variant.id: (variant, product, category)
        for variant, product, category in variant_rows
    }

    subtotal_cents = 0
    for item in cart_items:
        catalog_entry = catalog_by_variant.get(item.variant_id)
        if catalog_entry is None:
            db.rollback()
            raise ConflictError(
                code="VARIANT_UNAVAILABLE",
                message="A cart item is no longer available.",
            )
        variant, product, category = catalog_entry
        if (
            not category.is_active
            or product.status != "ACTIVE"
            or not variant.is_active
            or variant.currency != locked_cart.currency
        ):
            db.rollback()
            raise ConflictError(
                code="VARIANT_UNAVAILABLE",
                message=f"Item {variant.sku} is no longer available.",
                details={"variant_id": str(variant.id)},
            )

        stock = stocks_by_variant.get(variant.id)
        available = max(0, stock.on_hand - stock.reserved) if stock is not None else 0
        if available < item.quantity:
            db.rollback()
            raise ConflictError(
                code="INSUFFICIENT_STOCK",
                message=f"Requested quantity exceeds available stock for {variant.sku}.",
                details={
                    "variant_id": str(variant.id),
                    "requested": item.quantity,
                    "available": available,
                },
            )
        line_total = variant.price_cents * item.quantity
        if line_total > _INT32_MAX:
            db.rollback()
            raise ValidationAppError(
                code="VALIDATION_ERROR",
                message="Order total exceeds maximum supported amount.",
            )
        subtotal_cents += line_total
        if subtotal_cents > _INT32_MAX:
            db.rollback()
            raise ValidationAppError(
                code="VALIDATION_ERROR",
                message="Order total exceeds maximum supported amount.",
            )

    discount_cents = 0
    shipping_cents = (
        0
        if subtotal_cents >= settings.free_shipping_threshold_cents
        else settings.standard_shipping_cents
    )
    tax_cents = int(
        (
            Decimal(subtotal_cents - discount_cents)
            * Decimal(settings.tax_rate_bps)
            / Decimal(10000)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    total_cents = subtotal_cents - discount_cents + shipping_cents + tax_cents
    if any(
        amount > _INT32_MAX
        for amount in (subtotal_cents, shipping_cents, tax_cents, total_cents)
    ):
        db.rollback()
        raise ValidationAppError(
            code="VALIDATION_ERROR",
            message="Order total exceeds maximum supported amount.",
        )

    checkout_now = datetime.now(UTC)
    reservation_expires_at = checkout_now + RESERVATION_TTL
    guest_access_expires_at = (
        checkout_now + GUEST_ACCESS_TTL if auth is None else None
    )
    request_fingerprint = _compute_checkout_fingerprint(
        cart_id=locked_cart.id,
        items=[(item.variant_id, item.quantity) for item in cart_items],
        customer_email=payload.customer_email,
        shipping_address=shipping_dict,
        billing_address=billing_dict,
    )

    order = Order(
        order_number=_generate_order_number(checkout_now),
        user_id=auth.user.id if auth is not None else None,
        cart_id=locked_cart.id,
        customer_email=payload.customer_email,
        guest_token_version=1,
        guest_access_expires_at=guest_access_expires_at,
        guest_client_key_hash=guest_client_key_hash,
        status="PENDING_PAYMENT",
        payment_status="REQUIRES_PAYMENT",
        fulfillment_status="UNFULFILLED",
        currency=locked_cart.currency,
        subtotal_cents=subtotal_cents,
        discount_cents=discount_cents,
        shipping_cents=shipping_cents,
        tax_cents=tax_cents,
        total_cents=total_cents,
        idempotency_key=scoped_key,
        request_fingerprint=request_fingerprint,
        shipping_address_json=shipping_dict,
        billing_address_json=billing_dict,
        placed_at=checkout_now,
    )
    db.add(order)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raced_order = db.scalar(
            select(Order).where(Order.idempotency_key == scoped_key)
        )
        if raced_order is not None:
            raced_cart = _find_actor_active_cart(
                db,
                user_id=auth.user.id if auth is not None else None,
                guest_token_hash=guest_token_hash,
                now=datetime.now(UTC),
            )
            return (
                _handle_existing_checkout_idempotency(
                    db,
                    existing_order=raced_order,
                    active_cart=raced_cart,
                    customer_email=payload.customer_email,
                    shipping_dict=shipping_dict,
                    billing_dict=billing_dict,
                    settings=settings,
                ),
                True,
            )
        raise ConflictError(
            code="CHECKOUT_CONFLICT",
            message="Order checkout conflicted with another request; please retry.",
        ) from exc

    actor_user_id = auth.user.id if auth is not None else None
    created_items: list[OrderItem] = []

    for item in cart_items:
        variant, product, _category = catalog_by_variant[item.variant_id]
        stock = stocks_by_variant[item.variant_id]
        line_total = variant.price_cents * item.quantity

        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            sku_snapshot=variant.sku,
            product_title_snapshot=product.title,
            variant_title_snapshot=variant.title,
            attributes_snapshot_json=dict(variant.attributes_json),
            unit_price_cents=variant.price_cents,
            quantity=item.quantity,
            line_total_cents=line_total,
        )
        db.add(order_item)
        created_items.append(order_item)

        stock.reserved += item.quantity
        stock.version += 1

        db.add(
            InventoryReservation(
                order_id=order.id,
                variant_id=variant.id,
                quantity=item.quantity,
                status="ACTIVE",
                expires_at=reservation_expires_at,
            )
        )
        db.add(
            InventoryLedgerEntry(
                variant_id=variant.id,
                order_id=order.id,
                actor_user_id=actor_user_id,
                reason="RESERVE",
                delta_on_hand=0,
                delta_reserved=item.quantity,
                on_hand_after=stock.on_hand,
                reserved_after=stock.reserved,
                note=f"Order {order.order_number} checkout reservation",
            )
        )

    append_order_event(
        db,
        order_id=order.id,
        actor_user_id=actor_user_id,
        event_type="ORDER_PLACED",
        from_status=None,
        to_status="PENDING_PAYMENT",
        metadata_json={
            "item_count": sum(item.quantity for item in cart_items),
            "total_cents": total_cents,
        },
        now=checkout_now,
    )

    locked_cart.status = "CONVERTED"

    init_idempotency_key = hash_token(f"checkout-init:{order.id}")
    intent_result = payment_adapter.create_intent(
        order_number=order.order_number,
        amount_cents=total_cents,
        currency=order.currency,
        idempotency_key=init_idempotency_key,
    )
    initial_intent = PaymentIntent(
        order_id=order.id,
        provider=intent_result.provider,
        provider_intent_id=intent_result.provider_intent_id,
        idempotency_key=init_idempotency_key,
        request_fingerprint=compute_payment_intent_fingerprint(
            order.order_number,
            total_cents,
            order.currency,
        ),
        amount_cents=total_cents,
        currency=order.currency,
        status=intent_result.status,
    )
    db.add(initial_intent)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced_order = db.scalar(
            select(Order).where(Order.idempotency_key == scoped_key)
        )
        if raced_order is not None:
            raced_cart = _find_actor_active_cart(
                db,
                user_id=auth.user.id if auth is not None else None,
                guest_token_hash=guest_token_hash,
                now=datetime.now(UTC),
            )
            return (
                _handle_existing_checkout_idempotency(
                    db,
                    existing_order=raced_order,
                    active_cart=raced_cart,
                    customer_email=payload.customer_email,
                    shipping_dict=shipping_dict,
                    billing_dict=billing_dict,
                    settings=settings,
                ),
                True,
            )
        raise

    if auth is None:
        tracker.record(client_ip_key, order.id)

    return (
        _build_checkout_response_dto(
            db,
            order=order,
            settings=settings,
            intent=initial_intent,
            items=created_items,
            reservation_expires_at=reservation_expires_at,
        ),
        False,
    )


def _handle_existing_checkout_idempotency(
    db: Session,
    *,
    existing_order: Order,
    active_cart: Cart | None,
    customer_email: str,
    shipping_dict: dict[str, Any],
    billing_dict: dict[str, Any],
    settings: Settings,
) -> CheckoutOrderResponseDTO:
    if active_cart is not None and active_cart.id != existing_order.cart_id:
        active_items = list(
            db.scalars(
                select(CartItem).where(CartItem.cart_id == active_cart.id)
            ).all()
        )
        if active_items:
            db.rollback()
            raise ConflictError(
                code="IDEMPOTENCY_KEY_CONFLICT",
                message="Idempotency-Key was already used for a different cart.",
            )

    order_items = _load_order_items(db, existing_order.id)
    replay_fingerprint = _compute_checkout_fingerprint(
        cart_id=existing_order.cart_id or uuid.UUID(int=0),
        items=[(item.variant_id, item.quantity) for item in order_items],
        customer_email=customer_email,
        shipping_address=shipping_dict,
        billing_address=billing_dict,
    )
    if not hmac.compare_digest(replay_fingerprint, existing_order.request_fingerprint):
        db.rollback()
        raise ConflictError(
            code="IDEMPOTENCY_KEY_CONFLICT",
            message="Idempotency-Key was already used with a different checkout payload.",
        )

    dto = _build_checkout_response_dto(
        db,
        order=existing_order,
        settings=settings,
        items=order_items,
    )
    db.rollback()
    return dto
