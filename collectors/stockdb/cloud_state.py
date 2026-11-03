"""Portable source-only market snapshots stored as bounded GitHub release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).resolve().parent
TABLES = ('prices_daily', 'symbols', 'groups', 'group_meta', 'sector_defs', 'sector_map')
TAG = 'market-data-state'
SEED = 'market-seed-v1.zip'
STATE_NAME = re.compile(r'market-state-\d{8}T\d{6}Z-\d+-\d+\.zip')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def pack(db: Path, archive: Path):
    """Read the original DB without modifying it; derived tables are rebuilt."""
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest = {'schemaVersion': 1, 'createdAt': datetime.now(timezone.utc).isoformat(), 'tables': {}}
    with tempfile.TemporaryDirectory(dir=archive.parent) as tmp:
        with duckdb.connect(str(db), read_only=True) as con:
            con.execute("SET threads=2; SET memory_limit='4GB'")
            for table in TABLES:
                file = Path(tmp) / f'{table}.parquet'
                condition = " WHERE source NOT IN ('sector_idx', 'se_sector_idx')" if table == 'prices_daily' else ''
                # Identifiers are the fixed allowlist above; paths are parameters.
                con.execute(f"COPY (SELECT * FROM {table}{condition}) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(file)])
                rows = con.execute(f'SELECT count(*) FROM {table}{condition}').fetchone()[0]
                manifest['tables'][table] = {'rows': rows, 'sha256': sha256(file)}
            manifest['prices'] = [str(x) for x in con.execute(
                "SELECT min(date),max(date) FROM prices_daily WHERE source NOT IN ('sector_idx','se_sector_idx')").fetchone()]
        partial = archive.with_suffix('.partial')
        with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_STORED) as z:
            for table in TABLES:
                z.write(Path(tmp) / f'{table}.parquet', f'{table}.parquet')
            z.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        partial.replace(archive)
    print(json.dumps({'snapshotBytes': archive.stat().st_size, 'prices': manifest['prices'],
                      'priceRows': manifest['tables']['prices_daily']['rows']}))
    return manifest


def restore(archive: Path, db: Path):
    if db.exists():
        raise ValueError('Refusing to overwrite an existing market database')
    db.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=db.parent) as tmp:
        with zipfile.ZipFile(archive) as z:
            expected = {'manifest.json'} | {f'{table}.parquet' for table in TABLES}
            if set(z.namelist()) != expected or len(z.namelist()) != len(expected):
                raise ValueError('Snapshot has unexpected or duplicate files')
            if z.getinfo('manifest.json').file_size > 65536:
                raise ValueError('Snapshot manifest too large')
            manifest = json.loads(z.read('manifest.json'))
            if manifest.get('schemaVersion') != 1 or set(manifest.get('tables', {})) != set(TABLES):
                raise ValueError('Unsupported snapshot schema')
            for table in TABLES:
                file = Path(tmp) / f'{table}.parquet'
                if z.getinfo(file.name).file_size > 2_000_000_000:
                    raise ValueError('Snapshot table too large')
                with z.open(file.name) as source, file.open('wb') as dest:
                    import shutil
                    shutil.copyfileobj(source, dest, length=1024 * 1024)
                if sha256(file) != manifest['tables'][table]['sha256']:
                    raise ValueError(f'Snapshot checksum mismatch: {table}')
        partial = db.with_suffix('.restoring.duckdb')
        if partial.exists():
            raise ValueError('An unfinished restore already exists')
        from src.prices.db import connect
        with connect(partial) as con:
            con.execute("SET threads=2; SET memory_limit='4GB'")
            for table in TABLES:
                path = str(Path(tmp) / f'{table}.parquet')
                if table == 'prices_daily':
                    con.execute('INSERT INTO prices_daily SELECT * FROM read_parquet(?)', [path])
                else:
                    con.execute(f'CREATE TABLE {table} AS SELECT * FROM read_parquet(?)', [path])
                rows = con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                if rows != manifest['tables'][table]['rows']:
                    raise ValueError(f'Snapshot row count mismatch: {table}')
            con.execute('CHECKPOINT')
        partial.replace(db)
    print('Restored verified source tables:', manifest['prices'])
    return manifest


class GitHubState:
    def __init__(self, repo=None, token=None):
        self.repo = repo or os.environ['GITHUB_REPOSITORY']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', self.repo):
            raise ValueError('Invalid repository')
        self.token = token or os.environ['GH_TOKEN']

    def api(self, method, path, body=None, missing_ok=False):
        response = requests.request(method, f'https://api.github.com/repos/{self.repo}{path}',
            headers={'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2026-03-10'}, json=body, timeout=60)
        if missing_ok and response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f'GitHub {method} {path}: HTTP {response.status_code}')
        return response.json() if response.content else None

    def release(self, create=False):
        release = self.api('GET', f'/releases/tags/{TAG}', missing_ok=True)
        if release is None and create:
            release = self.api('POST', '/releases', {'tag_name': TAG, 'target_commitish': 'main',
                'name': 'Market data state', 'prerelease': True, 'make_latest': 'false',
                'body': 'Automated source-only market snapshots. Keeps the initial seed and the two most recent verified states. Collector code and usage: collectors/stockdb/README.md.'})
        if release is None:
            raise RuntimeError('Market data seed release is not initialized')
        return release

    def assets(self, release):
        return self.api('GET', f"/releases/{release['id']}/assets?per_page=100")

    def pull(self, archive, db):
        assets = self.assets(self.release())
        candidates = sorted((a for a in assets if a['state'] == 'uploaded' and STATE_NAME.fullmatch(a['name'])),
                            key=lambda a: a['name'], reverse=True)
        candidates += [a for a in assets if a['state'] == 'uploaded' and a['name'] == SEED]
        if not candidates:
            raise RuntimeError('No verified market state is available')
        # Fail loudly on a corrupt latest state; silently falling back could lose history.
        asset = candidates[0]
        archive.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(asset['browser_download_url'], stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            with archive.open('wb') as file:
                for block in response.iter_content(1024 * 1024):
                    file.write(block)
        if archive.stat().st_size != asset['size']:
            raise ValueError('Release download size mismatch')
        digest = asset.get('digest')
        if digest and digest != 'sha256:' + sha256(archive):
            raise ValueError('Release asset checksum mismatch')
        restore(archive, db)
        print('Source state:', asset['name'])

    def push(self, archive, seed=False):
        release = self.release(create=seed)
        assets = self.assets(release)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        name = SEED if seed else f"market-state-{stamp}-{os.environ.get('GITHUB_RUN_ID', '0')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}.zip"
        digest = 'sha256:' + sha256(archive)
        existing = next((a for a in assets if a['name'] == name), None)
        if existing:
            if existing.get('digest') == digest and existing['state'] == 'uploaded':
                return existing
            raise RuntimeError('A different snapshot already uses this name; refusing to overwrite it')
        url = f"https://uploads.github.com/repos/{self.repo}/releases/{release['id']}/assets"
        with archive.open('rb') as file:
            response = requests.post(url, params={'name': name}, data=file,
                headers={'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/zip',
                         'Content-Length': str(archive.stat().st_size)}, timeout=(30, 600))
        if response.status_code != 201:
            raise RuntimeError(f'Market state upload: HTTP {response.status_code}')
        asset = response.json()
        if asset['state'] != 'uploaded' or asset['size'] != archive.stat().st_size or (asset.get('digest') and asset['digest'] != digest):
            raise ValueError('Uploaded market state verification failed')
        # Only these pipeline-owned assets are rotated, after successful upload.
        if not seed:
            states = sorted((a for a in self.assets(release) if STATE_NAME.fullmatch(a['name']) and a['state'] == 'uploaded'),
                            key=lambda a: a['name'], reverse=True)
            for old in states[2:]:
                self.api('DELETE', f"/releases/assets/{old['id']}")
        print('Saved source state:', asset['name'], asset['size'], 'bytes')
        return asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['pack', 'restore', 'pull', 'push'])
    parser.add_argument('--db', type=Path, default=ROOT / 'data/market.duckdb')
    parser.add_argument('--archive', type=Path, default=ROOT / 'data/market-state.zip')
    args = parser.parse_args()
    if args.action == 'pack':
        pack(args.db, args.archive)
    elif args.action == 'restore':
        restore(args.archive, args.db)
    elif args.action == 'pull':
        GitHubState().pull(args.archive, args.db)
    else:
        GitHubState().push(args.archive)


if __name__ == '__main__':
    main()
