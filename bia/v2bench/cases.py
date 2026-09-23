"""벤치마크 사례. spec 의 표를 그대로 옮긴 것이며 데이터 생성 전에 고정됐다.

C01~C18 은 계획의 18 사례이고, C19~C24 는 수정 라운드 1 에서 닫은 커버리지
공백(그룹 bounded 거부·비율×교차 분해·support_ops additive·교차 한도 경계·
composition_dominant 와 simpson_strict 의 비동치)이다.

모든 수치는 손으로 검산 가능한 작은 정수다. oracle 이 exact arithmetic 으로
같은 값을 독립 계산할 수 있어야 하기 때문이다.

브리핑 원안에서 두 사례의 데이터를 고쳤다. 상수나 임계값은 건드리지 않았다.

* C04 — 원안의 golden (`delta=-0.10`, `entry_exit_effect=-0.10`) 은 원안의 데이터로
  달성할 수 없다. 진입 그룹의 net contribution 은 `N1g/D1 - 0` 이라 정의상 음수가
  될 수 없고, 원안처럼 진입 그룹의 분자가 0 이면 entry_exit 은 아예 0 이 되어
  `decomposition_complete` 도 참이 된다. 사례의 의도('진입 그룹 때문에 분해가
  닫히지 않는다')를 살리려고 데이터를 고쳤다.
* C09 — 원안의 데이터는 전체 분모까지 0 이라 `integrity` 이전에 `aggregate` 에서
  거부된다. 그룹 단위 무결성을 보려는 의도에 맞게 분모가 살아 있는 채널을 하나
  더 뒀다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Row:
    day: str
    keys: Tuple[Tuple[str, str], ...]
    measures: Tuple[Tuple[str, int], ...]


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    domain: str
    metric: str
    breakdowns: Tuple[str, ...]
    rank_by: str
    baseline: Tuple[str, str]      # (start, end) ISO
    current: Tuple[str, str]
    rows: Tuple[Row, ...]
    notes: str
    expect_refused: Optional[Tuple[str, str]] = None   # (stage, reason_substring)
    # 값은 int, float, 또는 `Fraction` 이 읽는 정확한 문자열("-1/60") 이다.
    # float 는 십진 표기 그대로(`Fraction(str(v))`) 해석하므로 1/60 같은 값은 문자열로 쓴다.
    golden: Dict[str, object] = field(default_factory=dict)
    source_csv: Optional[str] = None   # 기존 v1 시나리오를 바이트 그대로 재사용


def _row(day, channel, device, sessions, orders, category="all"):
    """ecommerce conversion_rate 행."""
    return Row(day=day,
               keys=(("category", category), ("channel", channel), ("device", device)),
               measures=(("orders", orders), ("sessions", sessions)))


def _rev(day, channel, device, category, revenue):
    """ecommerce revenue 행."""
    return Row(day=day,
               keys=(("category", category), ("channel", channel), ("device", device)),
               measures=(("revenue_krw", revenue),))


def _ops(day, queue, priority, received, resolved):
    """support_ops sla_resolution_rate 행. day 는 접수 cohort 다."""
    return Row(day=day,
               keys=(("priority", priority), ("queue", queue)),
               measures=(("received", received), ("resolved_within_sla", resolved)))


def _tickets(day, queue, priority, received):
    """support_ops tickets_received 행. additive metric 이라 컬럼은 received 하나다."""
    return Row(day=day,
               keys=(("priority", priority), ("queue", queue)),
               measures=(("received", received),))


B1 = ("2026-06-01", "2026-06-01")
C1 = ("2026-07-01", "2026-07-01")


# C01 mean(r) 과 ΣN/ΣD 가 갈린다.
C01 = CaseSpec(
    case_id="C01", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 10),
        _row("2026-06-01", "organic", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 200, 30),
        _row("2026-07-01", "organic", "mobile", 100, 10),
    ),
    notes=("paid 10/100→30/200, organic 20/100→10/100. "
           "ΣN/ΣD: 30/200=0.15 → 40/300=2/15, delta=-1/60=-0.0166667. "
           "mean(r): 0.15 → 0.125, delta=-0.025. 둘은 다르고 엔진은 ΣN/ΣD 를 쓴다. "
           "rate=-0.0125, mix=-1/240=-0.00416667, entry_exit=0. "
           "gross=7/60, |delta|/gross=1/7=0.1429<0.20 이라 heavy_cancellation 참."),
    golden={"delta": "-1/60"},
)

# C02 composition_dominant — 두 채널 모두 자기 전환율은 올랐는데
# 전환율이 낮은 쪽의 세션 점유가 크게 늘어 전체는 내려간다.
C02 = CaseSpec(
    case_id="C02", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 10),
        _row("2026-06-01", "organic", "mobile", 100, 30),
        _row("2026-07-01", "paid", "mobile", 300, 33),
        _row("2026-07-01", "organic", "mobile", 100, 31),
    ),
    notes=("baseline 40/200=0.20, current 64/400=0.16. 그룹 비율은 둘 다 상승 "
           "(0.10→0.11, 0.30→0.31). rate=+0.01, mix=-0.05, entry_exit=0."),
    golden={"delta": -0.04, "composition_dominant": True},
)

# C03 세 그룹 모두 Δr 이 양인데 전체는 음 — simpson_strict.
C03 = CaseSpec(
    case_id="C03", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 80),
        _row("2026-06-01", "organic", "mobile", 100, 50),
        _row("2026-06-01", "affiliate", "mobile", 100, 10),
        _row("2026-07-01", "paid", "mobile", 100, 82),
        _row("2026-07-01", "organic", "mobile", 100, 51),
        _row("2026-07-01", "affiliate", "mobile", 400, 44),
    ),
    notes=("Δr: 0.80→0.82, 0.50→0.51, 0.10→0.11 — 셋 다 양. "
           "전체 140/300=7/15=0.466667 → 177/600=0.295, delta=-0.171667. "
           "comparable 전부 같은 부호이고 전체는 반대라 simpson_strict 참, "
           "total_rate 부호도 delta 와 달라 composition_dominant 도 참이다."),
    golden={"simpson_strict": True, "composition_dominant": True},
)

# C04 진입 그룹 때문에 분해가 닫히지 않는다.
C04 = CaseSpec(
    case_id="C04", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "affiliate", "mobile", 100, 4),
    ),
    notes=("affiliate 는 baseline 에 없다. 전체 20/100=0.20 → 24/200=0.12, "
           "delta=-0.08. paid: rate=0, mix=-0.10, net=-0.10. "
           "affiliate: 비교 불가, net=4/200=+0.02 가 통째로 entry_exit. "
           "-0.10+0.02=-0.08 로 세 항 합은 맞지만 entry_exit≠0 이므로 "
           "decomposition_complete 는 거짓이고, 그 때문에 composition_dominant 도 거짓이다."),
    golden={"delta": -0.08, "entry_exit_effect": 0.02,
            "decomposition_complete": False, "composition_dominant": False},
)

# C05 이탈 그룹 (D1=0).
C05 = CaseSpec(
    case_id="C05", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-01", "partner", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 100, 10),
    ),
    notes=("partner 는 current 에 행이 없다 (D1=0). 전체 40/200=0.20 → 10/100=0.10, "
           "delta=-0.10. paid: rate=0.75*(-0.10)=-0.075, mix=0.15*0.5=+0.075, net=0. "
           "partner: net=0-20/200=-0.10 이 전부 entry_exit. decomposition_complete 거짓."),
    golden={"delta": -0.10, "entry_exit_effect": -0.10,
            "decomposition_complete": False},
)

# C06 상쇄가 큰 경우 — heavy_cancellation.
C06 = CaseSpec(
    case_id="C06", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 50, 40),
        _row("2026-06-01", "organic", "mobile", 50, 5),
        _row("2026-07-01", "paid", "mobile", 50, 10),
        _row("2026-07-01", "organic", "mobile", 50, 34),
    ),
    notes=("분모는 양 기간 100 으로 고정이라 mix=0 이고 net=Δ분자/100 이다. "
           "paid net=-0.30, organic net=+0.29. 전체 45/100=0.45 → 44/100=0.44, "
           "delta=-0.01. gross=0.59, |delta|/gross=0.016949<0.20 → heavy_cancellation 참. "
           "임계값 0.20 에서 충분히 멀다."),
    golden={"delta": -0.01, "heavy_cancellation": True},
)

# C07 |ΔR| < SHARE_EPSILON 인데 gross 는 크다.
C07 = CaseSpec(
    case_id="C07", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 10000, 8000),
        _row("2026-06-01", "organic", "mobile", 10000, 2000),
        _row("2026-07-01", "paid", "mobile", 10000, 2000),
        _row("2026-07-01", "organic", "mobile", 10000, 8001),
    ),
    notes=("분모 고정 20000. 10000/20000=0.5 → 10001/20000=0.50005, "
           "delta=1/20000=0.00005 로 SHARE_EPSILON=0.0001 보다 작다. "
           "gross=|-0.30|+|0.30005|=0.60005 로 크다. contribution_share 는 전부 억제된다."),
    golden={"delta": "1/20000"},
)

# C08 진입 + 구성 이동 동시. 세 항 모두 0 이 아니다.
C08 = CaseSpec(
    case_id="C08", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-01", "organic", "mobile", 100, 40),
        _row("2026-07-01", "paid", "mobile", 200, 50),
        _row("2026-07-01", "organic", "mobile", 100, 38),
        _row("2026-07-01", "affiliate", "mobile", 100, 10),
    ),
    notes=("전체 60/200=0.30 → 98/400=0.245, delta=-0.055. "
           "paid rate=+0.025 mix=0, organic rate=-0.0075 mix=-0.0975, "
           "affiliate net=10/400=+0.025 (entry_exit). "
           "total_rate=+0.0175, total_mix=-0.0975, entry_exit=+0.025 — 셋 다 0 이 아니다."),
    golden={"delta": -0.055, "entry_exit_effect": 0.025},
)

# C09 분모 0 인데 분자 > 0 — 세션 없이 주문이 있을 수 없다.
C09 = CaseSpec(
    case_id="C09", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-01", "organic", "mobile", 100, 10),
        _row("2026-07-01", "paid", "mobile", 0, 5),
        _row("2026-07-01", "organic", "mobile", 100, 10),
    ),
    notes=("organic 덕에 전체 분모는 양쪽 다 살아 있어 aggregate 단계는 통과하고, "
           "channel 분해에서 paid 가 걸린다. integrity 가 거부해야 한다. 보간하지 않는다."),
    expect_refused=("integrity", "denominator"),
)

# C10 전체 분모가 0.
C10 = CaseSpec(
    case_id="C10", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-07-01", "paid", "mobile", 0, 0),
    ),
    notes="current 의 전체 세션이 0 이다. 비율 자체가 정의되지 않아 aggregate 에서 거부한다.",
    expect_refused=("aggregate", "denominator"),
)

# C11 빈 차원값 → __UNKNOWN__, partition 보존.
C11 = CaseSpec(
    case_id="C11", domain="ecommerce", metric="revenue",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _rev("2026-06-01", "", "mobile", "books", 100),
        _rev("2026-06-01", "paid", "mobile", "books", 200),
        _rev("2026-07-01", "", "mobile", "books", 150),
        _rev("2026-07-01", "paid", "mobile", "books", 250),
    ),
    notes=("빈 channel 은 버려지지 않고 __UNKNOWN__ 그룹이 된다. "
           "300 → 400, delta=+100. __UNKNOWN__ +50, paid +50 으로 합이 전체와 같다."),
    golden={"delta": 100},
)

# C12 grain 중복 행 충돌.
C12 = CaseSpec(
    case_id="C12", domain="ecommerce", metric="revenue",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _rev("2026-06-01", "paid", "mobile", "books", 100),
        _rev("2026-06-01", "paid", "mobile", "books", 120),
        _rev("2026-07-01", "paid", "mobile", "books", 200),
    ),
    notes=("같은 (day, channel, device, category) 인데 매출이 다르다. "
           "어느 쪽이 참인지 데이터가 말해주지 않으므로 숫자를 내지 않는다."),
    expect_refused=("integrity", "conflicting duplicate rows"),
)


def _c13_rows():
    """26 channel × 8 device × 5 category = 1040 교차 셀 > CROSS_CELL_LIMIT(1000).

    한 셀은 한 기간에만 둔다. 교차 우주는 두 기간 키의 합집합이라 1040 그대로다.
    난수를 쓰지 않는다 — 인덱스의 결정론적 함수다.
    """
    rows: List[Row] = []
    index = 0
    for channel_i in range(26):
        for device_i in range(8):
            for category_i in range(5):
                day = "2026-06-01" if index % 2 == 0 else "2026-07-01"
                rows.append(_rev(
                    day,
                    "ch%02d" % channel_i,
                    "dv%d" % device_i,
                    "ct%d" % category_i,
                    100 + (index % 7),
                ))
                index += 1
    return tuple(rows)


# C13 교차 셀이 한도를 넘으면 교차 분기는 omitted.
C13 = CaseSpec(
    case_id="C13", domain="ecommerce", metric="revenue",
    breakdowns=("channel", "device", "category"), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=_c13_rows(),
    notes=("교차 셀 1040 개. 단일 차원 분기(26·8·5 그룹)는 정상이고 "
           "교차 분기만 omitted 로 빠진다. 값을 반올림하거나 상위 N 개로 자르지 않는다."),
)

# C14 additive metric 에 rate_effect 로 정렬을 요구한다.
C14 = CaseSpec(
    case_id="C14", domain="ecommerce", metric="revenue",
    breakdowns=("channel",), rank_by="rate_effect",
    baseline=B1, current=C1,
    rows=(
        _rev("2026-06-01", "paid", "mobile", "books", 100),
        _rev("2026-07-01", "paid", "mobile", "books", 200),
    ),
    notes=("additive metric 에는 rate_effect 가 없다. 실행이 아니라 컴파일에서 거부한다."),
    expect_refused=("compile", "rate_effect"),
)

# C15 부분 기간 — 분자와 분모가 같은 정렬 구간에서 나오는가.
C15 = CaseSpec(
    case_id="C15", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=("2026-06-01", "2026-06-03"), current=("2026-07-01", "2026-07-03"),
    rows=(
        # 창 밖 — 어느 쪽으로든 섞이면 결과가 크게 흔들리도록 극단값을 뒀다.
        _row("2026-05-31", "paid", "mobile", 1000, 1000),
        _row("2026-06-04", "organic", "mobile", 1000, 0),
        _row("2026-06-30", "paid", "mobile", 1000, 1000),
        _row("2026-07-04", "organic", "mobile", 1000, 0),
        # baseline 창
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-02", "paid", "mobile", 100, 20),
        _row("2026-06-03", "paid", "mobile", 100, 20),
        _row("2026-06-01", "organic", "mobile", 100, 10),
        _row("2026-06-02", "organic", "mobile", 100, 10),
        _row("2026-06-03", "organic", "mobile", 100, 10),
        # current 창
        _row("2026-07-01", "paid", "mobile", 100, 30),
        _row("2026-07-02", "paid", "mobile", 100, 30),
        _row("2026-07-03", "paid", "mobile", 100, 30),
        _row("2026-07-01", "organic", "mobile", 100, 10),
        _row("2026-07-02", "organic", "mobile", 100, 10),
        _row("2026-07-03", "organic", "mobile", 100, 10),
    ),
    notes=("창 안만 세면 90/600=0.15 → 120/600=0.20, delta=+0.05. "
           "창 밖 행이 한 줄이라도 섞이면 분자든 분모든 크게 틀어진다. "
           "paid rate=+0.05 mix=0, organic rate=mix=0."),
    golden={"delta": 0.05},
)

# C16 v1 시나리오 S01 을 그대로 통과시킨다.
C16 = CaseSpec(
    case_id="C16", domain="complaints", metric="complaint_count",
    breakdowns=("product", "complaint_type"), rank_by="net_contribution",
    baseline=("2026-06-01", "2026-06-30"), current=("2026-07-01", "2026-07-30"),
    rows=(),
    source_csv="data/scenarios/S01/metrics.csv",
    notes=("S01 의 metrics.csv 를 바이트 그대로 복사해 typed 실행 경로로 통과시킨다. "
           "golden 은 v1 사전등록 oracle(data/oracle/oracle.json 의 S01)의 값이며 "
           "여기서는 CSV 에서 정수로 다시 세어 맞춘다."),
    golden={"baseline": 1696, "current": 1903, "delta": 207},
)

# C17 단순 증가 + 신규 category.
C17 = CaseSpec(
    case_id="C17", domain="ecommerce", metric="revenue",
    breakdowns=("category",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _rev("2026-06-01", "paid", "mobile", "books", 100),
        _rev("2026-06-01", "paid", "mobile", "food", 200),
        _rev("2026-07-01", "paid", "mobile", "books", 150),
        _rev("2026-07-01", "paid", "mobile", "food", 220),
        _rev("2026-07-01", "paid", "mobile", "toys", 80),
    ),
    notes=("300 → 450, delta=+150. books +50, food +20, toys +80. "
           "additive 에서는 baseline 에 없던 그룹도 0 으로 비교되므로 분해가 닫힌다."),
    golden={"delta": 150},
)

# C18 baseline 분자 0 — relative_change 가 없다.
C18 = CaseSpec(
    case_id="C18", domain="support_ops", metric="sla_resolution_rate",
    breakdowns=("queue",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _ops("2026-06-01", "billing", "high", 100, 0),
        _ops("2026-06-01", "tech", "high", 100, 0),
        _ops("2026-07-01", "billing", "high", 100, 40),
        _ops("2026-07-01", "tech", "high", 100, 10),
    ),
    notes=("baseline 0/200=0, current 50/200=0.25, delta=+0.25. "
           "baseline 비율이 0 이라 relative_change 는 계산되지 않고 None 이다 — 0 이 아니다. "
           "billing rate=+0.20, tech rate=+0.05, mix 는 분모가 고정이라 0."),
    golden={"delta": 0.25},
)


# C19 분모는 살아 있는데 분자가 분모보다 크다 — bounded 계약 위반.
C19 = CaseSpec(
    case_id="C19", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-01", "organic", "mobile", 100, 10),
        _row("2026-07-01", "paid", "mobile", 10, 40),
        _row("2026-07-01", "organic", "mobile", 100, 10),
    ),
    notes=("current 의 paid 는 세션 10 에 주문 40 이다. 분모가 0 이 아니라서 "
           "분모0 검사(C09 가 밟는 가지)는 통과하고, conversion_rate 가 선언한 "
           "numerator_bounded_by_denominator 검사가 처음으로 걸린다. "
           "전체는 baseline 30/200, current 50/110 으로 분자<=분모라 aggregate 와 "
           "overall 분기를 통과한다 — 그래야 그룹 단계까지 내려간다. "
           "organic 은 전체 분모를 살려두는 역할이다."),
    expect_refused=("integrity", "bounded by the denominator"),
)

# C20 비율 지표 × 교차 분해. 엔진에서 가장 복잡한 경로다.
C20 = CaseSpec(
    case_id="C20", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel", "device"), rank_by="rate_effect",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "desktop", 100, 10),
        _row("2026-06-01", "paid", "mobile", 100, 20),
        _row("2026-06-01", "organic", "desktop", 100, 30),
        _row("2026-06-01", "organic", "mobile", 100, 40),
        _row("2026-07-01", "paid", "desktop", 100, 20),
        _row("2026-07-01", "paid", "mobile", 200, 60),
        _row("2026-07-01", "organic", "desktop", 100, 30),
        _row("2026-07-01", "organic", "mobile", 100, 55),
    ),
    notes=("전체 100/400=0.25 → 165/500=0.33, delta=+0.08. 교차 셀은 4 개뿐이라 "
           "한도(1000) 안에서 실제로 계산된다. 교차 셀 rate/mix: "
           "paid|desktop 0.0225/-0.0075, paid|mobile 0.0325/+0.0375, "
           "organic|desktop 0/-0.015, organic|mobile 0.03375/-0.02375. "
           "합은 total_rate=0.08875, total_mix=-0.00875, entry_exit=0. "
           "channel 분기의 total_rate=47/480 이 golden 이다. "
           "rank_by=rate_effect 라 세 분기 모두 rate_effect 로 정렬된다. "
           "네 rate 값은 일부러 서로 다르게 뒀다 — production 의 RANK 는 float 를 "
           "허용오차 없이 비교하므로, 수학적으로 같은 두 rate 는 1e-17 수준의 "
           "표현 오차로 순서가 갈리고 선언된 동률 규칙이 적용되지 않는다. "
           "자세한 내용은 task-5-fix1-report.md 의 F1."),
    golden={"delta": 0.08, "total_rate_effect": "47/480", "entry_exit_effect": 0},
)

# C21 support_ops 의 additive 지표를 priority 로 분해한다.
C21 = CaseSpec(
    case_id="C21", domain="support_ops", metric="tickets_received",
    breakdowns=("priority",), rank_by="group_delta",
    baseline=B1, current=C1,
    rows=(
        _tickets("2026-06-01", "billing", "high", 60),
        _tickets("2026-06-01", "tech", "high", 40),
        _tickets("2026-06-01", "billing", "low", 120),
        _tickets("2026-06-01", "tech", "low", 80),
        _tickets("2026-07-01", "billing", "high", 70),
        _tickets("2026-07-01", "tech", "high", 60),
        _tickets("2026-07-01", "billing", "low", 90),
        _tickets("2026-07-01", "tech", "low", 60),
        _tickets("2026-07-01", "billing", "urgent", 40),
    ),
    notes=("300 → 320, delta=+20. priority 별로 high 100→130 (+30), "
           "low 200→150 (-50), urgent 0→40 (+40). "
           "gross_movement=30+50+40=120. urgent 는 baseline 에 없지만 additive 라 "
           "0 으로 비교돼 분해가 닫힌다. rank_by=group_delta 로 정렬하면 "
           "urgent(+40), high(+30), low(-50) 순이다."),
    golden={"baseline": 300, "current": 320, "delta": 20, "gross_movement": 120},
)


def _grid_rows(channels, devices):
    """channel x device 교차 셀을 정확히 `channels * devices` 개 만든다.

    한 셀은 한 기간에만 둔다 — 교차 우주는 두 기간 키의 합집합이라 곱 그대로다.
    셀 수만 채우고 행은 셀당 하나로 유지한다. 난수는 없다.
    """
    rows: List[Row] = []
    index = 0
    for channel_i in range(channels):
        for device_i in range(devices):
            day = "2026-06-01" if index % 2 == 0 else "2026-07-01"
            rows.append(_rev(day, "ch%03d" % channel_i, "dv%03d" % device_i,
                             "all", 100 + (index % 7)))
            index += 1
    return tuple(rows)


# C22 교차 셀이 정확히 CROSS_CELL_LIMIT — 비교 연산자가 `>` 인지 `>=` 인지 고정한다.
C22 = CaseSpec(
    case_id="C22", domain="ecommerce", metric="revenue",
    breakdowns=("channel", "device"), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=_grid_rows(100, 10),
    notes=("교차 셀 100*10=1000 개로 CROSS_CELL_LIMIT 과 정확히 같다. "
           "한도는 초과(>)일 때만 걸리므로 교차 분기가 계산돼야 한다 — "
           "omitted 가 하나도 없어야 한다. C23 과 짝을 이뤄 경계를 고정한다."),
)

# C23 교차 셀이 한도+1 — 여기서부터 omitted.
C23 = CaseSpec(
    case_id="C23", domain="ecommerce", metric="revenue",
    breakdowns=("channel", "device"), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=_grid_rows(91, 11),
    notes=("교차 셀 91*11=1001 개로 한도보다 정확히 하나 많다. "
           "교차 분기만 omitted 이고 단일 차원 분기(91, 11 그룹)는 정상이다."),
)

# C24 composition_dominant 는 참인데 simpson_strict 는 거짓 — 함의가 동치가 아님을 보인다.
C24 = CaseSpec(
    case_id="C24", domain="ecommerce", metric="conversion_rate",
    breakdowns=("channel",), rank_by="net_contribution",
    baseline=B1, current=C1,
    rows=(
        _row("2026-06-01", "paid", "mobile", 100, 50),
        _row("2026-06-01", "organic", "mobile", 100, 10),
        _row("2026-06-01", "partner", "mobile", 100, 30),
        _row("2026-07-01", "paid", "mobile", 100, 60),
        _row("2026-07-01", "organic", "mobile", 300, 24),
        _row("2026-07-01", "partner", "mobile", 100, 30),
    ),
    notes=("전체 90/300=0.30 → 114/500=0.228, delta=-0.072. "
           "그룹 비율은 paid 0.50→0.60 (rate +2/75), organic 0.10→0.08 "
           "(rate -7/750), partner 0.30→0.30 (rate 0) 으로 부호가 섞여 있다. "
           "total_rate=13/750=+0.0173333 은 delta 와 부호가 반대라 "
           "composition_dominant 는 참이지만, comparable 의 rate 부호가 하나가 "
           "아니므로 simpson_strict 는 거짓이다. total_mix=-67/750, entry_exit=0. "
           "gross=76/750, |delta|/gross=54/76=0.711>0.20 이라 heavy 도 거짓."),
    golden={"delta": -0.072, "composition_dominant": True,
            "simpson_strict": False},
)


CASES: Tuple[CaseSpec, ...] = (
    C01, C02, C03, C04, C05, C06, C07, C08, C09,
    C10, C11, C12, C13, C14, C15, C16, C17, C18,
    C19, C20, C21, C22, C23, C24,
)
