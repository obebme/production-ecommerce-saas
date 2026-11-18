"""Pydantic schemas for Module 1 (Identity & Access Management)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

UserRole = Literal["customer", "staff", "admin"]


def _validate_and_strip_full_name(value: object) -> object:
    """Reject NUL and ASCII control characters before stripping outer whitespace."""
    if not isinstance(value, str):
        return value
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError("Full name must not contain control characters.")
    return value.strip()


class UserDTO(BaseModel):
    """Public/authenticated representation of a user account."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class AuthSessionResponse(BaseModel):
    """Response payload for register, login, and GET /auth/me."""

    user: UserDTO
    csrf_token: str


class UserUpdateResponse(BaseModel):
    """Response payload for PATCH /auth/me."""

    user: UserDTO


class RegisterRequest(BaseModel):
    """Customer self-registration payload (forbids role assignment)."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=10, max_length=128, repr=False)
    full_name: str = Field(min_length=1, max_length=120)

    @field_validator("email", mode="after")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) > 320:
            raise ValueError("Email exceeds maximum length of 320 characters.")
        return normalized

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_full_name(cls, value: object) -> object:
        return _validate_and_strip_full_name(value)


class LoginRequest(BaseModel):
    """Email + password login payload."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128, repr=False)

    @field_validator("email", mode="after")
    @classmethod
    def _normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class UpdateProfileRequest(BaseModel):
    """Profile update payload restricted strictly to full_name."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=120)

    @field_validator("full_name", mode="before")
    @classmethod
    def _strip_full_name(cls, value: object) -> object:
        return _validate_and_strip_full_name(value)
