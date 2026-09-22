# 내 데이터로 돌려보기

`custom-data-template/` 을 복사해 두 파일의 내용만 바꾸면, 번들된 시나리오와
**똑같은 파이프라인**(무결성 검사 → 지표 → 후보 생성 → 조회 → 검증 → 답변)이
그대로 돈다. 스키마는 하나뿐이고 별칭·자동 매핑은 없다.

```
python3 -m bia.cli run --data-dir ./examples/custom-data-template \
  --current 2026-07-01:2026-07-07 --baseline 2026-06-01:2026-06-07
```

## 두 파일 계약

디렉터리에 `metrics.csv` 와 `tickets.jsonl` 두 개만 있으면 된다.
`scenario.json` 은 필요 없다 — 질문의 구간은 `--current` / `--baseline` 에서 온다.

### metrics.csv

헤더는 아래 4개, **이 순서 그대로**여야 한다.

```
day,product,complaint_type,count
```

| 컬럼 | 규칙 |
|---|---|
| `day` | ISO 날짜 `YYYY-MM-DD` |
| `product` | 빈 값 불가 |
| `complaint_type` | 빈 값 불가 |
| `count` | 0 이상의 정수 |

### tickets.jsonl

한 줄에 JSON 객체 하나. 키는 **정확히** 아래 6개(더도 덜도 안 된다).

```
ticket_id, day, product, complaint_type, text, source
```

- `day` 는 ISO 날짜다. `created_at` 같은 다른 이름은 받지 않는다.
- 본문 키는 `text` 다. `body` 는 받지 않는다.
- `source` 는 `web_form`, `email`, `in_app` 중 하나여야 한다. 그 밖의 값은
  검증 단계에서 근거로 채택되지 않으므로, 실행 전에 오류로 막는다.

## 구간 인자

- 형식은 `START:END`, 양끝 포함.
- 현재 구간은 기준 구간이 끝난 뒤에 시작해야 한다.
- `--data-dir` 와 `--scenario` 는 함께 쓸 수 없고, 둘 중 하나는 있어야 한다.
- `--selector` 는 오프라인 코드 selector(`heuristic`, `greedy`)만 받는다.
  모델 selector 는 CLI 에서 실행할 수 없다.

## 검증 실패는 실행 전에 난다

파일·줄 번호·문제가 된 값·기대값을 함께 출력하고 멈춘다. 예:

```
데이터를 받을 수 없다 — .../tickets.jsonl:3: source 는 web_form, email, in_app 중 하나여야 한다. 받은 값: 'phone' (허용 밖 출처는 검증 단계에서 근거로 채택되지 않는다)
```

## 이 예시 데이터에 대해

`custom-data-template/` 은 형식을 보여주기 위한 손으로 만든 예시다. 평가
데이터가 아니고 어떤 채점 경로에도 들어가지 않는다. `eval/` 의 수치와는
무관하다.


## 값이 닫혀 있는 두 컬럼

자유 문자열이 아니다. 목록 밖 값은 **로드 시점에 오류**로 막는다 — 통과시키면 실행은 되는데 근거가 0건으로 끝나고, 이유를 알 길이 없다.

| 컬럼 | 허용 값 |
|---|---|
| `complaint_type` | `delivery_delay`, `billing_error`, `app_crash`, `damaged_item`, `support_wait` |
| `source` | `web_form`, `email`, `in_app` |

`complaint_type` 이 닫혀 있는 이유는 검증기가 **본문이 자기 라벨을 뒷받침하는지** 확인하기 때문이다. 그 판정은 유형별 용어 목록(`bia/lexicon.py`)에 의존하므로, 목록에 없는 유형은 어떤 본문으로도 뒷받침될 수 없다.

자기 유형을 쓰려면 `bia/lexicon.py` 의 `SUPPORT_TERMS` 에 그 유형과 용어를 추가해야 한다. 이 저장소는 그 확장을 지원하지 않는다 — 동결된 벤치마크가 이 5종에 묶여 있기 때문이다.

`product` 는 자유 문자열이다. 숫자가 들어간 이름(`Model-2024`)도 괜찮다.
