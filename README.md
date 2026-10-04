# 개인용 투자 데이터 대시보드 — 데이터 수집 v1

첫 단계는 화면 개발이 아니라 관세청 월간 수출입 API의 실제 응답을 작게 수집하고 검증하는 것입니다. 이 저장소는 다음 4개 API를 대상으로 합니다.

- 수출입총괄
- 품목별 수출입실적
- 품목별 국가별 수출입실적
- 국가별 수출입실적

원본 XML은 `data/raw/<endpoint>/`, 파싱한 CSV는 `data/processed/<endpoint>/`에 분리 저장합니다. 인증키는 저장하거나 로그에 출력하지 않습니다.

## 빠른 시작

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

`.env`의 `DATA_GO_KR_API_KEY`에 공공데이터포털 인증키를 입력한 뒤, 확정치가 존재하는 작은 기간으로 실행합니다.

```powershell
python -m investment_data.cli collect-all `
  --start-month 2026-06 `
  --end-month 2026-08 `
  --hs-code 8542323000 `
  --hs-code 8542311000 `
  --country-code US `
  --country-code CN
```

설치 없이 저장소에서 바로 실행하려면 `$env:PYTHONPATH='src'`를 먼저 설정합니다. 특정 API만 확인할 수도 있습니다.

```powershell
python -m investment_data.cli collect item-country-trade `
  --start-month 2026-08 --end-month 2026-08 `
  --hs-code 8542323000 --country-code US
```

응답 구조만 다시 분석하려면 API 호출 없이 저장된 XML을 사용합니다.

```powershell
python -m investment_data.cli inspect data/raw/item_trade/example.xml
```

테스트:

```powershell
python -m pytest
```

## 2010년 이후 로컬 백필

총괄·품목 전체·국가 전체는 조건을 생략한 전체조회가 가능하다. API의 1년 제한에 맞춰 자동으로 연도별 요청하고, 중단 후 같은 명령을 다시 실행하면 완료된 연도는 건너뛴다. 실제 호출로 확인한 총괄 최초월은 1989-01, 품목별 최초월은 1995-01이다.

```powershell
python -m investment_data.cli backfill --endpoint trade-summary `
  --start-month 1989-01 `
  --end-month 2026-08

python -m investment_data.cli backfill --endpoint item-trade `
  --start-month 1995-01 `
  --end-month 2026-08

python -m investment_data.cli backfill --endpoint country-trade `
  --start-month 1991-01 `
  --end-month 2026-08
```

원본은 `data/raw/backfill/`에 gzip XML, 분석용 데이터는 `data/local/backfill/`에 Zstandard 압축 Parquet로 저장한다. 두 디렉터리는 대용량 로컬 캐시이므로 Git에서 제외된다. 품목×국가 전체는 데이터 규모가 훨씬 커 별도 분할·용량 검증 후 수집한다.

국가 전체 백필에서 확인한 국가 코드 목록으로 품목×국가 데이터를 이어서 받을 수 있다. 국가·연도별 파일 단위로 재개되며, 기본적으로 디스크 여유가 5GB 아래로 내려가면 안전하게 중단한다.

```powershell
python -m investment_data.cli backfill-item-country `
  --start-month 2010-01 `
  --end-month 2026-08
```

## HS6 월별 데이터 생성

다운로드한 품목 전체 데이터에서 HS6 월별 집계를 생성한다. 정상 HS10은 앞 6자리로 묶고, 비표준 잔차는 `UNMAPPED_HS`, 수출입총괄과의 차이는 `UNALLOCATED_TO_HS`로 보존한다.

```powershell
python -m investment_data.cli build-hs6 --data-root data
```

결과는 `data/local/derived/hs6/`에 연도별 Parquet와 전체 기간 압축 CSV로 저장된다. 단가는 하위 품목 단가의 평균이 아니라 합산 금액을 합산 중량으로 나눠 다시 계산한다.

## 품목 대시보드 시계열 생성

전체 품목 백필에서 HS10·HS6 기준 월간 및 분기 시계열을 생성한다. 월간 데이터에는 수출금액·단가·일평균수출과 MoM/YoY가, 분기 데이터에는 같은 핵심 지표와 QoQ/YoY가 포함된다. 일평균수출은 달력 일수 기준이며, 미완료 분기는 값은 보존하되 성장률은 비워 둔다.

```powershell
python -m investment_data.cli build-item-timeseries --data-root data
```

결과는 `data/local/analytics/item-timeseries/`에 저장된다. 단가는 모든 집계 수준에서 `수출금액 합계 ÷ 수출중량 합계`로 다시 계산한다.
계산식과 컬럼 정의는 [docs/TIMESERIES_SCHEMA.md](docs/TIMESERIES_SCHEMA.md)에 정리되어 있다.

초기 백필 이후에는 원본 파일을 추가로 만들지 않고 API 응답에서 시계열을 직접 갱신한다. 정정치 반영을 위해 현재 연도 1월부터 최신 확정월까지 다시 조회하는 방식을 권장한다.

```powershell
python -m investment_data.cli update-item-timeseries `
  --start-month 2026-01 `
  --end-month 2026-08
```

네 종류(수출입총괄·품목별·국가별·품목×국가)를 모두 갱신할 때는 통합 명령을 사용한다. 조회 응답은 메모리에서 처리하고 `data/local/analytics/`의 시계열만 교체하므로 새 raw 파일을 만들지 않는다. 품목×국가는 국가 코드별 API 요청이 필요해 다른 세 종류보다 오래 걸린다.

```powershell
$env:PYTHONPATH='src'
python -m investment_data.cli update-export-timeseries `
  --start-month 2026-01 `
  --end-month 2026-08 `
  --data-root data
```

매달 최신 확정월이 나온 뒤 해당 연도 1월부터 다시 받으면 연중 정정치도 함께 반영된다. 한 요청은 API 제한에 맞춰 최대 12개월까지 가능하다. 성공 기록은 `data/local/analytics/last-update.json`에 남으며 인증키와 원본 응답은 저장하지 않는다.

## 나머지 대시보드 시계열 생성

수출입총괄·국가별·품목×국가 백필을 대시보드용 시계열로 변환한다.

```powershell
python -m investment_data.cli build-dashboard-timeseries --data-root data
```

수출입총괄과 국가별은 월·분기 파일로 저장한다. 품목×국가는 규모가 크므로 하나의 논리적 시계열 데이터셋을 연도와 HS 장으로 나눠 저장한다.

## 웹앱용 단일 데이터 파일 생성·검증

품목 매핑 원본은 `dashboard/catalog.json`, 산업 합계 규칙은 `dashboard/industry-rules.json`에서 수정한다. 아래 명령은 이 매핑과 로컬 시계열로 월간·분기 지표를 계산하고, 매핑·국가별 데이터·산업 합계까지 검증한 뒤 웹앱이 읽는 파일 하나를 생성한다. 오류가 있으면 기존 웹 파일은 유지된다.

```powershell
python scripts/build_dashboard_data.py
python scripts/validate_web_data.py
```

웹앱은 `dashboard/data/dashboard-data.json`만 읽는다. 데이터 형식과 매핑 수정 절차는 [dashboard/README.md](dashboard/README.md)에 정리했다.
지역 시계열은 `data/processed/region-series.json`을 재현 가능한 입력으로 사용하며, 이 파일이 없으면 웹 데이터 생성이 중단된다.

## 월간 자동 갱신

GitHub Actions용 [월간 갱신 워크플로](.github/workflows/update-export-dashboard.yml)를 추가했다. 매일 한국시간 20:37에 관세청의 대상월 **확정치 보도자료**를 확인하고, 발표가 확인된 경우에만 네 API를 조회해 웹용 JSON을 갱신·검증·커밋한다. GitHub 서버에서는 로컬 대용량 Parquet 없이 기존 웹 시계열에 새 월을 합친다. 현재 작업 폴더에는 Git 원격이 없어 GitHub에 올리고 API 키를 Repository Secret으로 설정해야 실제 예약 실행이 시작된다. 활성화 절차와 지역 데이터 제외 범위는 [docs/MONTHLY_AUTOMATION.md](docs/MONTHLY_AUTOMATION.md)를 참고한다.

현재 확인 결과와 다음 판단 항목은 [docs/API_FINDINGS.md](docs/API_FINDINGS.md)에 정리되어 있습니다.

## 디렉터리

```text
src/investment_data/   API 설정, HTTP 수집, XML 파싱, CSV 변환, CLI
tests/fixtures/        비밀값 없이 파서를 재현하는 최소 XML
data/raw/              원본 XML (Git 제외)
data/processed/        파싱 CSV (Git 제외)
docs/                  응답 포맷 조사 결과
```

## 보안 원칙

- 실제 키는 `.env` 또는 GitHub Actions Secrets에만 둡니다.
- 요청 URL에는 키가 포함되므로 전체 URL을 로그나 예외에 노출하지 않습니다.
- `.env`와 재생성 가능한 raw XML은 기본적으로 Git에서 제외합니다.
- 검증된 `dashboard/data/dashboard-data.json`은 웹앱이 GitHub에서 읽을 데이터이므로 커밋할 수 있습니다. API 키와 로컬 원본은 포함하지 않습니다.
- raw XML에는 현재 통계 응답만 저장되지만, 향후 다른 API를 추가할 때 개인정보 포함 여부를 다시 검토해야 합니다.
