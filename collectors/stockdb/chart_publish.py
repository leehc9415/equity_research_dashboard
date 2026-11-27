"""종목차트 데이터를 DB로 만들어 릴리스(market-charts)의 charts.zip 으로 올린다.

종목별 파일 2,700여 개(약 80MB)를 매일 커밋하면 저장소가 계속 커지므로 커밋하지 않는다.
사이트 배포(deploy-dashboard.yml)가 이 zip을 내려받아 dashboard/markets/charts/ 에 풀어 함께 배포한다.
GitHub API 로 올리므로 gh CLI 없이 PC(local_publish.py)와 Actions 양쪽에서 돈다 (GH_TOKEN, GITHUB_REPOSITORY).
"""
from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import duckdb
import requests

from cloud_state import GitHubState
from src.prices.db import DB_PATH
from src.dashboard import chart_export

TAG = 'market-charts'
NAME = 'charts.zip'


def upload(archive: Path, gh: GitHubState | None = None):
    """새 zip 을 임시 이름으로 올린 뒤 기존 charts.zip 을 지우고 이름을 바꾼다 → 배포가 받는 파일이 비는 순간이 없다."""
    gh = gh or GitHubState()
    release = gh.api('GET', f'/releases/tags/{TAG}', missing_ok=True) or gh.api('POST', '/releases', {
        'tag_name': TAG, 'target_commitish': 'main', 'name': 'Stock chart data (latest)', 'make_latest': 'false',
        'body': '종목차트 데이터. 국내 갱신 때마다 charts.zip 을 덮어쓰고, 사이트 배포가 내려받아 함께 배포합니다.'})
    assets = gh.api('GET', f"/releases/{release['id']}/assets?per_page=100")
    for a in assets:                                   # 지난번 중간에 실패한 임시 파일
        if a['name'] == 'charts-new.zip':
            gh.api('DELETE', f"/releases/assets/{a['id']}")
    with archive.open('rb') as f:
        r = requests.post(f"https://uploads.github.com/repos/{gh.repo}/releases/{release['id']}/assets",
                          params={'name': 'charts-new.zip'}, data=f, timeout=(30, 600),
                          headers={'Authorization': f'Bearer {gh.token}', 'Content-Type': 'application/zip',
                                   'Content-Length': str(archive.stat().st_size)})
    if r.status_code != 201 or r.json().get('size') != archive.stat().st_size:
        raise RuntimeError(f'charts.zip upload: HTTP {r.status_code}')
    new = r.json()
    for a in assets:
        if a['name'] == NAME:
            gh.api('DELETE', f"/releases/assets/{a['id']}")
    gh.api('PATCH', f"/releases/assets/{new['id']}", {'name': NAME})


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            info = chart_export.build(con, out)
            chart_export.validate(out)
            archive = out / NAME
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for path in sorted((out / 'charts').glob('*.js')):
                    z.write(path, path.name)
            print(f"charts.zip {archive.stat().st_size / 1e6:.1f}MB · {info['n']} stocks · as of {info['asof']}", flush=True)
            upload(archive)
            return info
    finally:
        con.close()


if __name__ == '__main__':
    main()
