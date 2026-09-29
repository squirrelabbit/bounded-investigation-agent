# CFPB 외부 검증 하네스

`v2.0.0` 분석 엔진(`bia/analysis/`)을 외부에서 만들어진 공개 데이터(CFPB Consumer Complaint Database)에
한 번 돌려, 입력 계약·데이터 품질 방어·연산 산술·출력 정책 플래그가 사전등록대로 동작하는지 본다.

## 주장 범위

- **보이는 것:** 외부 데이터 모양에서 엔진이 거부·경고·계산을 선언된 규칙대로 하는지(external-shape +
  robustness validation). 독립 oracle 과의 산술 일치.
- **보이지 않는 것:** "현실에서 분석이 맞다". CFPB 데이터에는 기여도의 정답이 없다. 이 결과를 production
  validity 근거로 쓰지 않는다.
- 요구사항 전부는 [`preregistration.md`](preregistration.md)(봉인, 수정 금지)에 있다.

## 실행

저장소 루트에서, Python 3.9+ 표준 라이브러리만 쓴다.

```bash
python3 validation/external/cfpb/fetch.py    # 원본을 /tmp/bia-cfpb-raw/ 로 (저장소 밖). results/acquisition.json
python3 validation/external/cfpb/loader.py   # 원본 → derived/*.csv, results/derivation.json
python3 validation/external/cfpb/run.py      # E1~E3, M1~M14 → results/*.json, results/summary.md
```

원본 CSV 와 소비자 서술문은 저장소에 쓰지 않는다. loader 는 allowlist 6개 열만 메모리에 남긴다.
CCDB 는 계속 갱신되므로 재다운로드하면 바이트가 달라질 수 있다 — 재현성은 받은 시점의 해시와 커밋된 파생
집계로 약속한다(5 MB 를 넘는 파생 파일은 커밋하지 않고 해시·행 수만 기록).

## 파일

| 파일 | 역할 |
|---|---|
| `fetch.py` | 사전등록 3절. 스트리밍 다운로드, URL·UTC 시각·SHA-256·행 수 기록 |
| `loader.py` | 4절. 유일 키·날짜 형식·Timely 값 검사, 파생 집계 두 개 |
| `spec.py` | 5·6절. 이 디렉터리 전용 `DomainSpec`, 실험 정의 |
| `oracle.py` | 8절. `bia` 를 import 하지 않는 `Fraction` 기반 독립 계산 |
| `mutations.py` | 10절. 손상 M1~M14 와 동작 분류 규칙(코드) |
| `run.py` | 실행·비교·요약 |

## 결과

[`results/summary.md`](results/summary.md) — 예측 P1~P7, 합격 기준 A1~A4, 손상 14종.

- [`findings.md`](findings.md) — 봉인된 결과 이후에 쓴 **사후 해석**. 예측이 빗나간 것, 판정과 실제 방어 능력이 다른 것,
  새로 드러난 외부 제약, 확인된 방어 공백. 결과 파일과 어긋나면 결과가 맞다.
- 획득 경로는 사전등록 이후 두 번 바뀌었다 — [`preregistration.md`](preregistration.md) 의 개정 1(검색 API → 공식 전체 파일),
  개정 2(성공한 요청 조건으로 다운로드 1회 재허용). 두 개정 모두 민원 레코드를 보기 전이다. 시도 기록은
  `results/acquisition_attempt1.json`(실패), `results/acquisition.json`(성공).
