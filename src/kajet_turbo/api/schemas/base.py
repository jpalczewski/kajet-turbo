"""Base class for REST request models that must preserve a legacy machine-readable
error code for a field-level failure Pydantic itself raises (missing required field,
wrong type, bad enum value) rather than a custom validator.

Declare `legacy_error_codes` per model instead of keying `api/errors.py`'s error-code
table by bare field name -- a `title`/`path`/`folder`/... collision between unrelated
models becomes structurally impossible because each model only ever contributes codes
for its own fields. `_request_validation_handler` (api/errors.py) resolves the model
that owns the failing field from the request's route and looks it up there.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from pydantic import BaseModel

from kajet_turbo.errors import ErrorCode


class RequestModel(BaseModel):
    legacy_error_codes: ClassVar[Mapping[str, ErrorCode]] = {}

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        unknown = set(cls.legacy_error_codes) - set(cls.model_fields)
        if unknown:
            raise TypeError(
                f"{cls.__name__}.legacy_error_codes references unknown field(s): {sorted(unknown)}"
            )
