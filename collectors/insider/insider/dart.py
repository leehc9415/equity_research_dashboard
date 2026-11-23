"""OpenDART API 클라이언트 (공시 목록, 공시 원문)."""
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://opendart.fss.or.kr/api"


class DartError(Exception):
    def __init__(self, status, message):
        super().__init__(f"[{status}] {message}")
        self.status = status


class LimitExceeded(DartError):
    """일일 호출 한도 초과(020). 수집을 멈추고 다음 실행에서 이어서 받는다."""


def load_api_key(root: Path) -> str:
    key = os.environ.get("OPENDART_API_KEY", "").strip()
    if not key:
        env = root / ".env"
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENDART_API_KEY"):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit(
            "OPENDART_API_KEY가 없습니다. 환경변수로 넣거나 .env 파일에 "
            "OPENDART_API_KEY=발급키 를 적어주세요."
        )
    return key


class Dart:
    def __init__(self, key: str, pause: float = 0.12, retries: int = 3):
        self.key = key
        self.pause = pause
        self.retries = retries
        self.calls = 0

    def _get(self, path: str, params: dict) -> bytes:
        q = urllib.parse.urlencode({"crtfc_key": self.key, **params})
        url = f"{BASE}/{path}?{q}"
        last = None
        for i in range(self.retries):
            try:
                time.sleep(self.pause)
                with urllib.request.urlopen(url, timeout=40) as r:
                    self.calls += 1
                    return r.read()
            except Exception as e:  # 네트워크 일시 오류는 재시도
                last = e
                time.sleep(2 * (i + 1))
        raise last

    def list_filings(self, date: str, pblntf_ty: str) -> list:
        """하루치 공시 목록 전체(페이지 끝까지)."""
        rows, page = [], 1
        while True:
            raw = self._get("list.json", {
                "bgn_de": date, "end_de": date, "pblntf_ty": pblntf_ty,
                "page_no": page, "page_count": 100,
            })
            d = json.loads(raw.decode("utf-8"))
            st = d.get("status")
            if st == "013":  # 조회된 데이터 없음
                return rows
            if st == "020":
                raise LimitExceeded(st, d.get("message"))
            if st != "000":
                raise DartError(st, d.get("message"))
            rows += d.get("list", [])
            if page >= int(d.get("total_page", 1)):
                return rows
            page += 1

    def document(self, rcept_no: str) -> bytes:
        """공시 원문(zip 안의 본문 파일 1개)을 bytes로 돌려준다."""
        raw = self._get("document.xml", {"rcept_no": rcept_no})
        if raw[:2] != b"PK":  # zip이 아니면 오류 응답(XML 또는 JSON)
            txt = raw.decode("utf-8", errors="replace")
            m = re.search(r'<status>(\d+)</status>|"status"\s*:\s*"(\d+)"', txt)
            status = (m.group(1) or m.group(2)) if m else "?"
            if status == "020":
                raise LimitExceeded(status, "사용한도 초과")
            raise DartError(status, txt[:200])
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = sorted(z.namelist(), key=lambda n: (not n.startswith(rcept_no), n))
        return z.read(names[0])
