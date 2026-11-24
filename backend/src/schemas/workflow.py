from typing import Any

from pydantic import BaseModel, Field


class LoginForm(BaseModel):
    username: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=1, max_length=128)


class CaseForm(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class ReviewForm(BaseModel):
    note: str = Field(min_length=1, max_length=500)


class AmendmentForm(BaseModel):
    contract_id: str = Field(min_length=1, max_length=64)
    asset_id: str = Field(min_length=1, max_length=64)
    hold_until: str | None = Field(default=None, max_length=10)
    effective_from: str = Field(min_length=10, max_length=10)
    effective_to: str | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=500)
    request_key: str | None = Field(default=None, min_length=1, max_length=128)


class RecomputeForm(BaseModel):
    request_key: str | None = Field(default=None, min_length=1, max_length=128)
