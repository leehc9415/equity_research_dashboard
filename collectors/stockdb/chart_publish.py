"""종목차트 데이터를 클라우드 DB로 만들어 릴리스(market-charts)의 charts.zip 으로 올린다.

종목별 파일 2,700여 개(약 80MB)를 매일 커밋하면 저장소가 계속 커지므로 커밋하지 않는다.
사이트 배포(deploy-dashboard.yml)가 이 zip을 내려받아 dashboard/markets/charts/ 에 풀어 함께 배포한다.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import zipfile
from pathlib import Path

import duckdb

from src.prices.db import DB_PATH
from src.dashboard import chart_export

TAG = 'market-charts'


def gh(*args, check=True):
    return subprocess.run(['gh', *args], check=check, capture_output=not check)


def main():
    repo = os.environ['GITHUB_REPOSITORY']
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            info = chart_export.build(con, out)
            chart_export.validate(out)
            archive = out / 'charts.zip'
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                for path in sorted((out / 'charts').glob('*.js')):
                    z.write(path, path.name)
            print(f"charts.zip {archive.stat().st_size / 1e6:.1f}MB · {info['n']} stocks · as of {info['asof']}", flush=True)
            if gh('release', 'view', TAG, '--repo', repo, check=False).returncode != 0:
                gh('release', 'create', TAG, '--repo', repo, '--latest=false', '--title', 'Stock chart data (latest)',
                   '--notes', '종목차트 데이터. 국내 갱신 때마다 charts.zip 을 덮어쓰고, 사이트 배포가 내려받아 함께 배포합니다.')
            gh('release', 'upload', TAG, str(archive), '--clobber', '--repo', repo)
    finally:
        con.close()


if __name__ == '__main__':
    main()
