"""Pydantic schemas and strict input validators for Module 2 (Product Catalog)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

ProductStatus = Literal["DRAFT", "ACTIVE", "ARCHIVED"]
ProductSortOrder = Literal["newest", "price_asc", "price_desc", "title_asc"]

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_INT32_MAX = 2_147_483_647
_INT32_MIN = -2_147_483_648


def ensure_utc_datetime(value: datetime) -> datetime:
    """Normalize naive datetimes (e.g. from SQLite) to explicit UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


UTCDatetime = Annotated[datetime, AfterValidator(ensure_utc_datetime)]


def strip_single_line(value: object, *, field_name: str) -> object:
    """Reject ASCII control characters and strip outer whitespace on single-line inputs."""
    if not isinstance(value, str):
        return value
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError(f"{field_name} must not contain control characters.")
    return value.strip()


def validate_safe_multiline(value: object, *, field_name: str) -> object:
    """Reject NUL bytes and unsafe control characters while permitting newlines and tabs."""
    if not isinstance(value, str):
        return value
    for ch in value:
        code = ord(ch)
        if code == 0 or (code < 0x20 and ch not in ("\n", "\r", "\t")) or code == 0x7F:
            raise ValueError(f"{field_name} must not contain unsafe control characters.")
    return value


def normalize_and_validate_slug(value: object) -> object:
    """Normalize and validate lowercase kebab-case URL-safe slugs."""
    if not isinstance(value, str):
        return value
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("Slug must not contain control characters.")
    normalized = value.strip().lower()
    if not SLUG_RE.fullmatch(normalized):
        raise ValueError(
            "Slug must be lowercase kebab-case alphanumeric segments separated by hyphens."
        )
    return normalized


def _has_unsafe_url_chars(candidate: str) -> bool:
    return (
        any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate)
        or ".." in candidate
        or "\\" in candidate
    )


def validate_product_image_url(value: object) -> object:
    """Validate product image URLs strictly to safe https:// URLs or /assets/ paths."""
    if not isinstance(value, str):
        return value
    candidate = value.strip()
    if not candidate:
        raise ValueError("Image URL must not be empty.")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch.isspace() for ch in candidate):
        raise ValueError("Image URL must not contain whitespace or control characters.")

    decoded_pass = candidate
    if _has_unsafe_url_chars(decoded_pass):
        raise ValueError(
            "Image URL must not contain control characters, path traversal, or backslashes."
        )
    for _ in range(3):
        decoded_pass = unquote(decoded_pass)
        if _has_unsafe_url_chars(decoded_pass):
            raise ValueError(
                "Image URL must not contain control characters, path traversal, or backslashes."
            )

    if candidate.startswith("https://"):
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Image URL is malformed.") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.netloc.endswith(":")
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("HTTPS image URL must specify a valid host without credentials.")
        if port is not None and (port < 1 or port > 65535):
            raise ValueError("HTTPS image URL contains an invalid port.")
        return candidate

    if candidate.startswith("/assets/"):
        try:
            parsed = urlsplit(candidate)
        except ValueError as exc:
            raise ValueError("Image URL is malformed.") from exc
        if (
            parsed.scheme != ""
            or parsed.netloc != ""
            or not parsed.path.startswith("/assets/")
            or len(parsed.path) <= len("/assets/")
        ):
            raise ValueError("Relative image URL must be a valid /assets/<path> reference.")
        return candidate

    raise ValueError("Image URL must start with 'https://' or '/assets/'.")


class CategoryPublicDTO(BaseModel):
    """Storefront representation of an active category."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    description: str | None
    sort_order: int


class CategoryListResponse(BaseModel):
    """Storefront category list response envelope."""

    items: list[CategoryPublicDTO]


class AdminCategoryDTO(BaseModel):
    """Administrative representation of a category."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parent_id: uuid.UUID | None
    name: str
    slug: str
    description: str | None
    is_active: bool
    sort_order: int
    created_at: UTCDatetime
    updated_at: UTCDatetime


class AdminCategoryListResponse(BaseModel):
    """Administrative category list response envelope."""

    items: list[AdminCategoryDTO]


class CategorySummaryDTO(BaseModel):
    """Compact category summary embedded inside product responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str


class ProductImageDTO(BaseModel):
    """Product image representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    alt_text: str
    sort_order: int
    is_primary: bool


class ProductVariantPublicDTO(BaseModel):
    """Storefront variant representation with server-computed availability."""

    id: uuid.UUID
    sku: str
    title: str
    attributes: dict[str, Any]
    price_cents: int
    compare_at_price_cents: int | None
    currency: str
    is_active: bool
    available_quantity: int
    in_stock: bool
    low_stock: bool


class ProductSummaryDTO(BaseModel):
    """Storefront catalog product summary card DTO."""

    id: uuid.UUID
    title: str
    slug: str
    category: CategorySummaryDTO
    primary_image: ProductImageDTO | None
    min_price_cents: int
    max_price_cents: int
    currency: str
    in_stock: bool


class ProductDetailDTO(BaseModel):
    """Storefront product detail DTO."""

    id: uuid.UUID
    title: str
    slug: str
    description: str
    status: ProductStatus
    category: CategorySummaryDTO
    images: list[ProductImageDTO]
    variants: list[ProductVariantPublicDTO]


class AdminVariantDTO(BaseModel):
    """Administrative variant representation with full inventory counts."""

    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    title: str
    attributes: dict[str, Any]
    price_cents: int
    compare_at_price_cents: int | None
    currency: str
    is_active: bool
    on_hand: int
    reserved: int
    available_quantity: int
    low_stock_threshold: int
    in_stock: bool
    low_stock: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime


class AdminProductSummaryDTO(BaseModel):
    """Administrative product listing summary DTO across DRAFT, ACTIVE, and ARCHIVED."""

    id: uuid.UUID
    title: str
    slug: str
    status: ProductStatus
    category: CategorySummaryDTO
    primary_image: ProductImageDTO | None
    min_price_cents: int | None
    max_price_cents: int | None
    currency: str | None
    variants_count: int
    active_variants_count: int
    total_available_quantity: int
    in_stock: bool
    created_at: UTCDatetime
    updated_at: UTCDatetime


class AdminProductDetailDTO(BaseModel):
    """Administrative product editor DTO including category, images, and all variants."""

    id: uuid.UUID
    title: str
    slug: str
    description: str
    status: ProductStatus
    category: CategorySummaryDTO
    images: list[ProductImageDTO]
    variants: list[AdminVariantDTO]
    created_at: UTCDatetime
    updated_at: UTCDatetime


class CreateCategoryRequest(BaseModel):
    """Request payload for POST /api/v1/admin/categories."""

    model_config = ConfigDict(extra="forbid")

    parent_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=5000)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=_INT32_MIN, le=_INT32_MAX)

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: object) -> object:
        return strip_single_line(value, field_name="Category name")

    @field_validator("slug", mode="before")
    @classmethod
    def _validate_slug(cls, value: object) -> object:
        return normalize_and_validate_slug(value)

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, value: object) -> object:
        return validate_safe_multiline(value, field_name="Category description")


class UpdateCategoryRequest(BaseModel):
    """Request payload for PATCH /api/v1/admin/categories/{category_id}."""

    model_config = ConfigDict(extra="forbid")

    parent_id: uuid.UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=5000)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=_INT32_MIN, le=_INT32_MAX)

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: object) -> object:
        return strip_single_line(value, field_name="Category name")

    @field_validator("slug", mode="before")
    @classmethod
    def _validate_slug(cls, value: object) -> object:
        return normalize_and_validate_slug(value)

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, value: object) -> object:
        return validate_safe_multiline(value, field_name="Category description")

    @model_validator(mode="after")
    def _reject_null_required_fields(self) -> UpdateCategoryRequest:
        for field_name in ("name", "slug", "is_active", "sort_order"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"Field '{field_name}' cannot be null.")
        return self


class ProductImageInput(BaseModel):
    """Input schema for product images on create/update."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    alt_text: str = Field(default="", max_length=200)
    sort_order: int = Field(default=0, ge=_INT32_MIN, le=_INT32_MAX)
    is_primary: bool = False

    @field_validator("url", mode="before")
    @classmethod
    def _validate_url(cls, value: object) -> object:
        return validate_product_image_url(value)

    @field_validator("alt_text", mode="before")
    @classmethod
    def _validate_alt_text(cls, value: object) -> object:
        return strip_single_line(value, field_name="Image alt_text")


def _validate_images_single_primary(
    images: list[ProductImageInput] | None,
) -> list[ProductImageInput] | None:
    if images is not None and sum(1 for img in images if img.is_primary) > 1:
        raise ValueError("At most one product image can have is_primary=true.")
    return images


class CreateProductRequest(BaseModel):
    """Request payload for POST /api/v1/admin/products."""

    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=220)
    description: str = Field(default="", max_length=20000)
    status: ProductStatus = "DRAFT"
    images: list[ProductImageInput] = Field(default_factory=list, max_length=20)

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> object:
        return strip_single_line(value, field_name="Product title")

    @field_validator("slug", mode="before")
    @classmethod
    def _validate_slug(cls, value: object) -> object:
        return normalize_and_validate_slug(value)

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, value: object) -> object:
        return validate_safe_multiline(value, field_name="Product description")

    @field_validator("images")
    @classmethod
    def _validate_images(
        cls, value: list[ProductImageInput]
    ) -> list[ProductImageInput]:
        validated = _validate_images_single_primary(value)
        return validated if validated is not None else []


class UpdateProductRequest(BaseModel):
    """Request payload for PATCH /api/v1/admin/products/{product_id}."""

    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    slug: str | None = Field(default=None, min_length=1, max_length=220)
    description: str | None = Field(default=None, max_length=20000)
    status: ProductStatus | None = None
    images: list[ProductImageInput] | None = Field(default=None, max_length=20)

    @field_validator("title", mode="before")
    @classmethod
    def _validate_title(cls, value: object) -> object:
        return strip_single_line(value, field_name="Product title")

    @field_validator("slug", mode="before")
    @classmethod
    def _validate_slug(cls, value: object) -> object:
        return normalize_and_validate_slug(value)

    @field_validator("description", mode="before")
    @classmethod
    def _validate_description(cls, value: object) -> object:
        return validate_safe_multiline(value, field_name="Product description")

    @field_validator("images")
    @classmethod
    def _validate_images(
        cls, value: list[ProductImageInput] | None
    ) -> list[ProductImageInput] | None:
        return _validate_images_single_primary(value)

    @model_validator(mode="after")
    def _reject_null_fields(self) -> UpdateProductRequest:
        for field_name in ("category_id", "title", "slug", "description", "status", "images"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"Field '{field_name}' cannot be null.")
        return self
