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

## 재현

| | |
|---|---|
| 엔진 | `v2.0.0` (`76bdb1f`) — 이 검증 동안 바뀌지 않았다 |
| 재현 진입점 | `3d961e7` (하네스 문서까지 포함한 마지막 커밋) |
| 봉인 경계 | `78576a9` (결과 봉인) |
| 입력 | 봉인 당시 원본 ZIP 그 자체 — SHA-256 은 `results/acquisition.json` 의 `sha256_zip` |

main 의 엔진은 v2.1 에서 `run_plan(plan, rows)` 를 없앴으므로 이 하네스는 main 에서 돌지 않는다. 봉인 자산이라 고치지 않는다.

CFPB 는 매일 갱신되는 파일만 제공하고 과거 스냅샷을 제공하지 않는다. 다시 받아서는 같은 입력을 얻지 못하므로 `fetch.py` 는
재현에 쓰지 않는다(받은 바이트 수가 봉인 당시 `Content-Length` 와 다르면 멈추도록 봉인돼 있다). 봉인 당시 ZIP 은 저장소 밖에
보관한다(소비자 서술문 포함, 100 MB 초과).

**일회용 clone 에서만 실행한다.** 이 하네스의 범위 가드는 작업 트리를 로컬 `main` 과 비교하므로 로컬 `main` 을 먼저 `3d961e7` 로
맞춰야 한다.

```bash
git clone <repo> cfpb-repro && cd cfpb-repro
git checkout -B main 3d961e7
mkdir -p /tmp/bia-cfpb-raw && cp <보관한 complaints.csv.zip> /tmp/bia-cfpb-raw/
python3 -c "import json,hashlib; e=json.load(open('validation/external/cfpb/results/acquisition.json'))['sha256_zip']; a=hashlib.sha256(open('/tmp/bia-cfpb-raw/complaints.csv.zip','rb').read()).hexdigest(); print('일치' if a==e else 'STOP: 다름')"
python3 validation/external/cfpb/run.py
```

재현 결과 (2026-10-01, 일회용 clone, 동일 조건 2회): 결과 JSON 12개 중 11개는 봉인본과 바이트 단위로 일치했다. `mutations.json` 1개는 로더 오류 메시지 5개(M9·M10·M12×2·M13)에 `tempfile.mkdtemp(prefix="bia-cfpb-mut-")`가 생성한 임시 디렉터리 경로가 포함되어 실행마다 바이트가 달랐다. 해당 경로 부분만 `<TMPDIR>`로 정규화하면 봉인본과 두 재현 결과가 모두 동일하며, 판정·수치·사전등록 예측 결과는 실행 간 변하지 않았다. 봉인된 v2.0 하네스와 결과는 수정하지 않는다.

(비교 대상 12개 중 `acquisition.json`·`acquisition_attempt1.json` 은 `run.py` 가 읽기만 하는 획득 기록이다. 비교에서 뺀 파일: `run_meta.json`(실행 시각·HEAD), `summary.md`.)
