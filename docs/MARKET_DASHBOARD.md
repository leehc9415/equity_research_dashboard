# 증시데이터

Investment Dashboard의 **증시데이터** 메뉴(`#markets`)는 `dashboard/markets/index.html#kr`을 표시합니다. 한국 업종·구성 종목, 미국 지수·섹터, 상품·통화·금리, 일·주·월봉, 검색·정렬은 stockDB 원본 화면을 그대로 사용합니다.

웹에서 PC의 `file:///C:/...` 주소를 직접 읽지 않고, stockDB가 내보낸 HTML과 필요한 데이터 파일을 함께 배포합니다. DB, 설정, 수집 코드 및 API 키는 복사하지 않습니다.

## 데이터 갱신

현재 배포는 복사한 시점의 스냅샷입니다. stockDB의 매일 수집 작업과 GitHub Pages 배포는 자동 연결되어 있지 않습니다. 각 탭의 데이터 기준일은 화면에 표시됩니다.

stockDB에서 데이터를 다시 내보낸 뒤, 이 저장소 루트에서 실행합니다.

```powershell
python scripts/sync_market_dashboard.py "C:\Users\leehc\Documents\stockDB\site"
git add dashboard/markets
git commit -m "Refresh market charts snapshot"
git push origin main
```

`dashboard/**` 변경 시 기존 Deploy investment dashboard 작업이 수출·캘린더 검증 후 전체 대시보드를 배포합니다. 다른 PC에서는 stockDB/site 경로를 해당 PC의 경로로 바꿉니다.

## PNG 복사

증시데이터 상단의 **PNG 복사** 버튼은 현재 보이는 영역을 PNG 이미지로 클립보드에 복사합니다. 이미지에서는 차트 영역 바깥의 양옆·아래 빈 여백을 잘라내며 가장자리에 8px만 남깁니다. 원하는 앱에 붙여넣을 수 있습니다. 브라우저가 이미지 클립보드 쓰기를 지원하지 않거나 복사를 차단하면 **PNG 저장** 버튼으로 같은 이미지를 내려받습니다. 이미지는 브라우저 안에서 생성되며 외부 서비스로 전송하지 않습니다.

`market-capture.js`가 화면이 열릴 때 도구를 추가하므로 stockDB 화면을 다시 동기화해도 복사 버튼이 유지됩니다. 포함된 html2canvas 1.4.1의 MIT 라이선스는 `dashboard/vendor/html2canvas-LICENSE.txt`에 있습니다.
