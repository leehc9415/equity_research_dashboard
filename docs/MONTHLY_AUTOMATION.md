# 수출 대시보드 월간 자동 갱신

관세청 안내상 전월 **월간 확정치 발표 기준일은 매월 15일**이다. 실제로 2026년 8월 확정치는 9월 15일, 2026년 1월 확정치는 2월 19일 게시됐다. 따라서 특정 날짜에 데이터를 무조건 덮어쓰지 않는다. 이 저장소는 매일 한국시간 20:37에 GitHub Actions를 실행하되, 해당 월의 관세청 보도자료에 정확히 `월간 수출입 현황 [확정치]`가 게시된 경우에만 진행한다. 15일의 휴일 여부를 자체 달력으로 추정하지 않고 실제 게시를 확인한다.

공식 근거: [관세청 발표 주기 안내](https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do?bbsId=2280&mi=10600&nttSn=10141577&nttSnUrl=862449616390be79d16b6d008088964d), [2026년 8월 확정치 게시물](https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do?bbsId=1362&mi=2891&nttSn=10176743&nttSnUrl=0b6477a0d2c425948fd0866c7b527f24), [2026년 1월 확정치 게시물](https://www.customs.go.kr/kcs/na/ntt/selectNttInfo.do?bbsId=1362&mi=2891&nttSn=10155383&nttSnUrl=742024c1fb310dbe8a9429ba57538682).

## 동작 방식

1. 현재 웹 데이터의 `asOf` 다음 달을 갱신 대상으로 잡는다. 발표가 늦거나 예약 실행이 누락돼도 다음 실행에서 같은 달을 다시 확인한다.
2. 관세청 확정치 보도자료를 확인한다. API에는 확정 발표 전에도 새 월 값이 보일 수 있으므로 API에 행이 있다는 사실만으로 확정치로 간주하지 않는다.
3. 해당 연도 1월부터 대상월까지 수출입총괄·품목별·국가별·품목×국가 API를 다시 조회한다. 품목×국가는 국가 코드별로 조회한다. 2월 확정월을 갱신할 때는 전년도 연간 정정을 반영하기 위해 전년도 1~12월도 다시 조회한다. 각 API 요청은 12개월을 넘지 않는다.
4. 저장소에 있는 `dashboard/data/dashboard-data.json`의 과거 시계열과 새 응답을 합친다. 총수출 일평균·MoM·YoY, 산업/세부산업, 품목 월간 단가·성장률과 분기 QoQ·YoY, 품목×국가를 재계산한다. 전체 1.5GB 로컬 Parquet를 GitHub에 올리지 않는다.
5. API 대상월 누락, 국가별 금액 합계 불일치, 매핑 충돌 또는 웹 데이터 검증 실패 시 기존 웹 파일은 그대로 유지하고 실행을 실패 처리한다. 다음 날 자동 재시도할 수 있다. 성공 시 검증된 JSON만 커밋한다.

현재 `regions`는 과거 Excel에서 만들어진 정적 자료이므로 **이 자동 갱신 대상이 아니다**. 지역 화면은 마지막 보유월까지만 표시된다. 시도별 품목 API의 코드·행정구역 개편 매핑을 검증한 후 별도 연결해야 한다.

## GitHub에서 활성화

GitHub 저장소에 워크플로와 웹 데이터를 올린 뒤 아래 설정을 마치면 예약 실행과 웹 배포가 활성화된다.

1. 이 저장소를 GitHub 저장소의 기본 브랜치에 올린다. `.github/workflows/update-export-dashboard.yml`과 `dashboard/data/dashboard-data.json`이 반드시 포함돼야 한다. `.env`와 `data/local`, `data/raw`는 올리지 않는다.
2. GitHub 저장소의 **Settings → Secrets and variables → Actions → Repository secrets**에서 `DATA_GO_KR_API_KEY`를 만든다. 채팅이나 코드에 키를 다시 넣지 않는다.
3. **Actions**에서 `Update finalized export dashboard`를 `Run workflow`로 한 번 수동 실행해 성공 및 `대기: ... 확정치 발표 전` 메시지를 확인한다. 이후 매일 예약 실행된다.
4. 저장소 정책상 쓰기 권한이 막혀 있으면 **Settings → Actions → General → Workflow permissions**를 확인한다. 워크플로는 `contents: write`만 요청한다.
5. GitHub Pages로 이 정적 대시보드를 배포할 경우 Pages의 **Build and deployment → Source**를 **GitHub Actions**로 선택하고, Actions 변수 `ENABLE_EXPORT_PAGES=true`를 설정한다. 워크플로를 한 번 수동 실행하면 새 확정월이 없어도 최초 사이트가 배포된다. 이후 자동 커밋이 발생할 때 같은 워크플로에서 재배포한다. 아직 Pages를 쓰지 않으면 이 변수는 설정하지 않는다.

수동 확인 또는 재시도:

```powershell
$env:PYTHONPATH='src'
python scripts/monthly_web_update.py
python scripts/validate_web_data.py
```

위 명령은 저장소의 웹용 JSON만 갱신한다. 로컬 전체 Parquet도 동기화하려면 기존 `update-export-timeseries` 명령을 별도로 실행하고 `build_dashboard_data.py`로 웹 파일을 다시 만들어야 한다. GitHub에서 만든 새 월분을 이 PC의 로컬 Parquet에 자동 역동기화하지는 않는다.

## 운영상 주의

- 이름·표시 산업·기업 같은 **표시 메타데이터만** 바꾼 경우 자동 갱신 시 새 매핑을 반영할 수 있다. HS코드나 활성 품목 목록, 산업 집계 규칙을 바꾼 경우에는 과거 전체 재계산이 필요하므로 로컬에서 `build_dashboard_data.py`를 실행해 새 JSON을 함께 올린다. 불일치한 상태에서는 자동 갱신이 멈추고 기존 데이터는 보존된다.
- GitHub 예약 실행은 정확한 분에 시작되지 않거나 누락될 수 있다. 그래서 매일 재시도하며, 한 달이 밀리면 `asOf` 다음 월부터 따라잡는다. 공공 저장소에서 활동이 장기간 없으면 GitHub가 예약 실행을 비활성화할 수 있으므로 Actions 상태를 가끔 확인한다.
- API 키는 이미 채팅에 공유된 적이 있으므로 GitHub Secret 등록 전 공공데이터포털에서 재발급해 기존 키를 폐기하는 편이 안전하다.
