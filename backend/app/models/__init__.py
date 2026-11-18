"""SQLAlchemy ORM model registry exporting all 17 tables for Alembic discovery."""

from app.core.database import Base
from app.models.cart import Cart, CartItem
from app.models.catalog import Category, Product, ProductImage
from app.models.identity import AuditLog, AuthSession, User
from app.models.inventory import (
    InventoryLedgerEntry,
    InventoryReservation,
    InventoryStock,
    ProductVariant,
)
from app.models.order import Order, OrderEvent, OrderItem
from app.models.payment import PaymentIntent, PaymentWebhookEvent

__all__ = [
    "AuditLog",
    "AuthSession",
    "Base",
    "Cart",
    "CartItem",
    "Category",
    "InventoryLedgerEntry",
    "InventoryReservation",
    "InventoryStock",
    "Order",
    "OrderEvent",
    "OrderItem",
    "PaymentIntent",
    "PaymentWebhookEvent",
    "Product",
    "ProductImage",
    "ProductVariant",
    "User",
]
