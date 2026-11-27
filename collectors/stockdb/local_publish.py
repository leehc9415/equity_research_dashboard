"""PC: 한국 시세 지표·대시보드를 로컬 DB 로 만들어 바로 게시한다 (run_daily.bat 이 키움 수집 뒤 실행).

  1. 레포가 main 이고 추적 파일 변경이 없는지 확인 → 최신 main 받기
  2. cloud_update.py --region kr --local : 한국 지수·지표 재계산·한국 파일 생성(해외 그룹은 게시본 유지)·검증
  3. chart_publish : 종목차트 zip → market-charts 릴리스
  4. dashboard/markets/data 커밋·push → deploy-dashboard.yml 이 push 로 바로 배포
push 가 다른 자동 커밋과 부딪히면 최신 main 으로 맞춘 뒤 한국 파일만 다시 만들어(--export-only) 재시도한다.
이 레포 폴더는 예약 작업 전용으로 둔다(추적 파일 변경이 있으면 아무것도 하지 않고 멈춤).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
DATA = 'dashboard/markets/data'


def git(*args, check=True):
    print('$ git', *args, flush=True)
    return subprocess.run(['git', '-C', str(REPO), *args], check=check, text=True, capture_output=not check)


def build(export_only=False):
    cmd = [sys.executable, '-u', 'cloud_update.py', '--region', 'kr', '--local'] + (['--export-only'] if export_only else [])
    subprocess.run(cmd, cwd=ROOT, check=True)


def main():
    branch = git('rev-parse', '--abbrev-ref', 'HEAD', check=False).stdout.strip()
    dirty = git('status', '--porcelain', '--untracked-files=no', check=False).stdout.strip()
    if branch != 'main' or dirty:
        sys.exit(f'[!] 레포가 main 이 아니거나 커밋 안 된 변경이 있어 게시하지 않습니다 (branch={branch}).\n{dirty}')
    git('pull', '--ff-only', 'origin', 'main')
    build()
    import chart_publish
    chart_publish.main()
    for attempt in range(3):
        git('add', DATA)
        if git('diff', '--cached', '--quiet', check=False).returncode == 0:
            print('No changed Korean market data.')
            return
        git('commit', '-m', 'Update Korean market data (PC)')
        if git('push', 'origin', 'HEAD:main', check=False).returncode == 0:
            print('Pushed; deploy-dashboard.yml deploys the site.')
            return
        # 다른 자동 커밋(해외·매크로 등)이 먼저 들어옴 → 최신 main 위에서 한국 파일만 다시 만든다
        git('fetch', 'origin', 'main')
        git('reset', '--hard', 'origin/main')
        build(export_only=True)
    sys.exit('[!] push 3번 실패')


if __name__ == '__main__':
    main()
