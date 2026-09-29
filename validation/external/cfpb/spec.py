"""사전등록 5·6절: 이 디렉터리 전용 도메인 선언과 실험 정의.

`bia/domains/` 에 두지 않는다. `register_all()` 을 부를 때만 레지스트리에 들어간다.
"""
from __future__ import annotations

import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from bia.analysis import registry  # noqa: E402
from bia.analysis.spec import DomainSpec, MetricSpec  # noqa: E402

PRODUCT_ISSUE = DomainSpec(
    name="cfpb_product_issue",
    grain=("day", "product", "issue"),
    dimensions=("product", "issue"),
    metrics={
        "complaint_count": MetricSpec(name="complaint_count", kind="additive",
                                      value="complaints"),
        "timely_rate": MetricSpec(name="timely_rate", kind="ratio",
                                  numerator="timely_yes", denominator="timely_known",
                                  numerator_bounded_by_denominator=True),
    },
)

COMPANY_PRODUCT = DomainSpec(
    name="cfpb_company_product",
    grain=("day", "company", "product"),
    dimensions=("company", "product"),
    metrics={
        "complaint_count": MetricSpec(name="complaint_count", kind="additive",
                                      value="complaints"),
    },
)

# 실험 정의. oracle 은 bia 를 import 할 수 없으므로 이 dict 를 JSON 으로 받는다 —
# 그래서 여기에는 순수 데이터만 둔다.
EXPERIMENTS = (
    {"id": "E1", "domain": "cfpb_product_issue", "file": "product_issue.csv",
     "grain_dimensions": ["product", "issue"],
     "baseline": ["2024-02-01", "2024-02-28"], "current": ["2024-03-01", "2024-03-28"],
     "breakdowns": ["product", "issue"],
     "metrics": {"complaint_count": {"kind": "additive", "columns": ["complaints"]},
                 "timely_rate": {"kind": "ratio", "columns": ["timely_yes", "timely_known"]}}},
    {"id": "E2", "domain": "cfpb_product_issue", "file": "product_issue.csv",
     "grain_dimensions": ["product", "issue"],
     "baseline": ["2017-03-27", "2017-04-23"], "current": ["2017-04-24", "2017-05-21"],
     "breakdowns": ["product", "issue"],
     "metrics": {"complaint_count": {"kind": "additive", "columns": ["complaints"]},
                 "timely_rate": {"kind": "ratio", "columns": ["timely_yes", "timely_known"]}}},
    {"id": "E3", "domain": "cfpb_company_product", "file": "company_product.csv",
     "grain_dimensions": ["company", "product"],
     "baseline": ["2024-02-01", "2024-02-28"], "current": ["2024-03-01", "2024-03-28"],
     "breakdowns": ["company", "product"],
     "metrics": {"complaint_count": {"kind": "additive", "columns": ["complaints"]}}},
)

DOMAINS = {PRODUCT_ISSUE.name: PRODUCT_ISSUE, COMPANY_PRODUCT.name: COMPANY_PRODUCT}


def register_all() -> None:
    registry.register(PRODUCT_ISSUE)
    registry.register(COMPANY_PRODUCT)
