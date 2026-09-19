from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class ContractModel(BaseModel):
    """Base for public contract models.

    Unknown fields are rejected so clients cannot inject server-owned authorship, roles,
    provenance, retention, or execution fields.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        validate_assignment=True,
    )
