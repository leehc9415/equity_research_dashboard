# 품목 대시보드 시계열 스키마

기준일: 2026-10-03

## 출력 파일

`data/local/analytics/item-timeseries/`에 다음 파일을 생성한다.

- `hs10_monthly.parquet`
- `hs10_quarterly.parquet`
- `hs6_monthly.parquet`
- `hs6_quarterly.parquet`
- `manifest.json`

파일은 품목코드와 기간 순으로 정렬하며 Zstandard로 압축한다. 전체 원본 보존용 파일과 대시보드 분석용 파일은 분리한다.

## 계산 원칙

- 월 일평균수출: `월 수출금액 / 해당 월의 달력 일수`
- 분기 일평균수출: `분기 수출금액 합계 / 해당 분기의 달력 일수`
- 수출단가: `수출금액 합계 / 수출중량 합계`
- MoM: 정확히 1개월 전 값과 비교
- 월 YoY: 정확히 12개월 전 값과 비교
- QoQ: 정확히 1분기 전 값과 비교
- 분기 YoY: 정확히 4분기 전 값과 비교
- 비교기간이 없거나 비교값이 0이면 성장률은 결측값으로 둔다.
- 미완료 분기는 금액·중량·단가·일평균 값을 보존하되 QoQ와 YoY는 계산하지 않는다.

## 월간 핵심 컬럼

- `date`, `year`, `quarter`
- `hs_level`, `hs_code`, `product_name`(HS10)
- `period_status`, `days_in_period`
- `export_value_usd`, `export_weight_kg`
- `export_unit_price_usd_per_kg`, `avg_daily_export_usd`
- 수출금액·단가·일평균수출의 `mom_pct`, `yoy_pct`
- 수입금액·중량, 무역수지, 원천 행 수

## 분기 핵심 컬럼

- `period`, `year`, `quarter`
- `is_complete_period`, `months_available`, `months_with_trade`
- 수출금액·중량·단가·일평균수출
- 수출금액·단가·일평균수출의 `qoq_pct`, `yoy_pct`
- 수입금액·중량, 무역수지, 원천 행 수

## 표시 범위

전체 HS 데이터는 분석 파일에 보존한다. 대시보드 초기 화면에서 HS 01~15와 41~60을 제외하는 정책은 공개용 JSON 생성 단계에 적용한다.

## 기타 대시보드 시계열

### 수출입총괄

`data/local/analytics/trade-summary-timeseries/`

- `monthly.parquet`: 월 수출입금액·무역수지·일평균수출과 MoM/YoY
- `quarterly.parquet`: 분기 합계·일평균수출과 QoQ/YoY

### 국가별

`data/local/analytics/country-timeseries/`

- `monthly.parquet`: 국가별 월 수출입금액·무역수지·일평균수출과 MoM/YoY
- `quarterly.parquet`: 국가별 분기 합계·일평균수출과 QoQ/YoY

### 품목×국가

`data/local/analytics/item-country-timeseries/hs10-monthly/`

- 논리적으로 하나의 월별 시계열이며 물리적으로 `year`, `chapter`로 분할한다.
- 수출입금액·중량·무역수지·수출단가·일평균수출을 저장한다.
- 선택 품목의 국가별 MoM/YoY는 대시보드 공개용 JSON을 생성할 때 계산한다.
- 향후 월간 갱신에서는 현재 연도 파티션만 교체한다.

## 월간 직접 업데이트

초기 백필은 복구용으로 동결한다. 이후에는 API 응답을 별도 XML·연도별 Parquet로 저장하지 않고 메모리에서 파싱하여 시계열 파일에 직접 upsert한다.

```powershell
python -m investment_data.cli update-item-timeseries `
  --start-month 2026-01 `
  --end-month 2026-09 `
  --data-root data
```

- 요청기간의 기존 월·HS코드 행은 새 응답으로 교체한다.
- 전체 월간 비교지표와 분기 집계를 다시 계산한다.
- 쓰기 도중 기존 정상 파일이 손상되지 않도록 임시 파일 완성 후 교체한다.
- 관세청 정정치를 반영하기 위해 최신월 한 달만 추가하기보다 현재 연도 1월부터 최신 확정월까지 갱신하는 것을 기본 운영 방식으로 삼는다.
