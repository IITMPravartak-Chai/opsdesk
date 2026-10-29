"""Field specifications for the configuration console resources: parsing, validation and serialisation.

Every resource in ``config_resources`` is described declaratively with these fields, so the generic handlers in
``routers/admin_config.py`` give each of them the same behaviour: tenant scoping, per-field validation with field-keyed
errors, audit events and safe deletes.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from sqlalchemy import select

from . import admin_rules as rules

SLUG = re.compile(r"^[-a-zA-Z0-9_]+$")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

MISSING = object()


class FieldError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class F:
    """One writable (or read-only) attribute of a resource.

    kind: str | text | int | bool | choice | slug | color | email | fk | str_list | domain_list | json_list |
          json_dict | id_list | prefix
    """

    def __init__(self, kind: str, *, required: bool = False, nullable: bool = False, max_len: Optional[int] = None,
                 min: Optional[int] = None, max: Optional[int] = None, choices=None, default: Any = MISSING,
                 model=None, scope: Optional[Callable] = None, label: str = "name", check: Optional[Callable] = None,
                 lower: bool = False, upper: bool = False, admin_only: bool = False, attr: Optional[str] = None,
                 read_only: bool = False, secret: bool = False, help: str = ""):
        self.kind, self.required, self.nullable = kind, required, nullable
        self.max_len, self.min, self.max = max_len, min, max
        self.choices, self.default = choices, default
        self.model, self.scope, self.label_attr = model, scope, label
        self.check, self.lower, self.upper = check, lower, upper
        self.admin_only, self.attr, self.read_only, self.secret = admin_only, attr, read_only, secret
        self.help = help

    # ------------------------------------------------------------------ clean
    def clean(self, session, tid: int, value: Any) -> Any:
        if value is None:
            if self.nullable:
                return None
            if self.required:
                raise FieldError("This field is required.")
            raise FieldError("This field may not be null.")
        k = self.kind
        if k in ("str", "slug", "color", "email", "prefix"):
            if not isinstance(value, str):
                raise FieldError("Enter text.")
            v = " ".join(value.split()) if k == "str" else value.strip()
            if self.lower:
                v = v.lower()
            if self.upper or k == "prefix":
                v = v.upper()
            if not v and not self.nullable:
                if self.required or k != "str":
                    raise FieldError("This field is required.")
            if self.max_len and len(v) > self.max_len:
                raise FieldError(f"Ensure this has at most {self.max_len} characters.")
            if k == "slug" and not SLUG.match(v):
                raise FieldError("Use letters, numbers, underscores or hyphens only.")
            if k == "color" and not COLOR.match(v):
                raise FieldError("Use a colour like #1a2b3c.")
            if k == "email" and v and not EMAIL.match(v):
                raise FieldError("Enter a valid email address.")
            if k == "prefix" and not re.match(r"^[A-Z0-9]{1,6}$", v):
                raise FieldError("Use 1 to 6 letters or digits.")
            return self._choice(v) if self.choices else self._custom(v)
        if k == "text":
            if not isinstance(value, str):
                raise FieldError("Enter text.")
            if self.max_len and len(value) > self.max_len:
                raise FieldError(f"Ensure this has at most {self.max_len} characters.")
            return self._custom(value)
        if k == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise FieldError("Enter a whole number.")
            if self.min is not None and value < self.min:
                raise FieldError(f"Ensure this is at least {self.min}.")
            if self.max is not None and value > self.max:
                raise FieldError(f"Ensure this is at most {self.max}.")
            return self._choice(value) if self.choices else self._custom(value)
        if k == "bool":
            if not isinstance(value, bool):
                raise FieldError("Must be true or false.")
            return value
        if k == "choice":
            return self._choice(value)
        if k == "fk":
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise FieldError("Choose a valid option.")
            stmt = select(self.model).where(self.model.id == value)
            if hasattr(self.model, "tenant_id"):
                stmt = stmt.where(self.model.tenant_id == tid)
            if self.scope:
                stmt = self.scope(stmt, session, tid)
            obj = session.scalars(stmt).first()
            if obj is None:
                raise FieldError("Choose a valid option.")
            return obj.id
        if k == "id_list":
            if not isinstance(value, list) or len(value) > 500:
                raise FieldError("Choose from the list.")
            ids = []
            for i in value:
                if isinstance(i, bool) or not isinstance(i, int) or i < 1:
                    raise FieldError("Choose from the list.")
                if i not in ids:
                    ids.append(i)
            if ids:
                stmt = select(self.model.id).where(self.model.id.in_(ids))
                if hasattr(self.model, "tenant_id"):
                    stmt = stmt.where(self.model.tenant_id == tid)
                if self.scope:
                    stmt = self.scope(stmt, session, tid)
                if len(set(session.scalars(stmt))) != len(ids):
                    raise FieldError("Choose from the list.")
            return sorted(ids)
        if k == "str_list":
            if not isinstance(value, list) or len(value) > 200 or not all(isinstance(x, str) for x in value):
                raise FieldError("Enter a list of text values.")
            out = []
            for x in value:
                x = x.strip().lower() if self.lower else x.strip()
                if not x:
                    continue
                if self.max_len and len(x) > self.max_len:
                    raise FieldError(f"Each value may have at most {self.max_len} characters.")
                if x not in out:
                    out.append(x)
            if self.choices and any(x not in self.choices for x in out):
                raise FieldError("Choose from the list.")
            return self._custom(out)
        if k == "domain_list":
            if not isinstance(value, list) or len(value) > 100 or not all(isinstance(x, str) for x in value):
                raise FieldError("Enter a list of domains.")
            out = []
            for x in value:
                x = x.strip().lower().lstrip("@")
                if not x:
                    continue
                if not DOMAIN.match(x):
                    raise FieldError(f"'{x}' is not a valid domain.")
                if x not in out:
                    out.append(x)
            return out
        if k == "json_list":
            if not isinstance(value, list) or len(value) > 500:
                raise FieldError("Enter a list.")
            return self._custom(value)
        if k == "json_dict":
            if not isinstance(value, dict):
                raise FieldError("Enter an object.")
            if len(repr(value)) > 100_000:
                raise FieldError("This is too large.")
            return self._custom(value)
        raise FieldError("Unsupported field.")

    def _choice(self, v):
        if v not in self.choices:
            raise FieldError("Choose a valid option.")
        return v

    def _custom(self, v):
        if self.check:
            out = self.check(v)
            return v if out is None else out
        return v

    # ------------------------------------------------------------------ describe (for the UI)
    def describe(self) -> dict:
        d: dict = {"kind": self.kind, "required": self.required, "nullable": self.nullable}
        for key in ("max_len", "min", "max"):
            if getattr(self, key) is not None:
                d[key] = getattr(self, key)
        if self.choices:
            d["choices"] = list(self.choices)
        if self.model is not None:
            d["ref"] = self.model.__name__
        if self.admin_only:
            d["admin_only"] = True
        if self.read_only:
            d["read_only"] = True
        if self.help:
            d["help"] = self.help
        return d


def collect(session, tid: int, fields: dict, data: dict, *, partial: bool, is_admin: bool, current: Any = None) -> dict:
    """Clean ``data`` against ``fields``. Raises RuleError(400) with a field-keyed ``errors`` map; admin-only fields
    sent by anyone but a manager are refused with 403."""
    out: dict = {}
    errors: dict = {}
    unknown = [k for k in data if k not in fields]
    if unknown:
        raise rules.RuleError(400, "Unknown field(s): " + ", ".join(sorted(unknown)),
                              {"errors": {k: "Unknown field." for k in unknown}})
    for name, spec in fields.items():
        if spec.read_only:
            if name in data:
                errors[name] = "This field is read-only."
            continue
        if name not in data:
            if not partial and spec.required and spec.default is MISSING:
                errors[name] = "This field is required."
            elif not partial and spec.default is not MISSING:
                out[name] = spec.default() if callable(spec.default) else spec.default
            continue
        if spec.admin_only and not is_admin:
            raise rules.RuleError(403, f"Only managers can change '{name}'.")
        try:
            out[name] = spec.clean(session, tid, data[name])
        except FieldError as e:
            errors[name] = e.message
    if errors:
        raise rules.RuleError(400, "Fix the highlighted fields.", {"errors": errors})
    return out


def label_of(obj) -> str:
    return str(obj)
