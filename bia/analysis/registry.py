"""선언된 도메인의 닫힌 목록. 런타임에 도메인을 발명할 수 없다."""
from __future__ import annotations

from typing import Dict, List

from .errors import RequestError, SpecError
from .spec import DomainSpec

_DOMAINS: Dict[str, DomainSpec] = {}


def register(spec: DomainSpec) -> None:
    existing = _DOMAINS.get(spec.name)
    if existing is not None and existing != spec:
        raise SpecError("domain %r is already registered with a different spec" % spec.name)
    _DOMAINS[spec.name] = spec


def get_domain(name: str) -> DomainSpec:
    try:
        return _DOMAINS[name]
    except KeyError:
        raise RequestError(
            "unknown domain %r; registered: %s" % (name, sorted(_DOMAINS))
        )


def registered_names() -> List[str]:
    return sorted(_DOMAINS)
