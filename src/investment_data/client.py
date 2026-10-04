"""HTTP client that saves raw responses before producing CSV files."""

from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .api_catalog import Endpoint
from .parser import parse_xml


class ConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class CollectionResult:
    endpoint: str
    raw_paths: tuple[Path, ...]
    csv_path: Path
    rows: int
    columns: tuple[str, ...]


def load_api_key() -> str:
    value = os.getenv("DATA_GO_KR_API_KEY", "").strip()
    if not value:
        raise ConfigurationError(
            "DATA_GO_KR_API_KEY가 없습니다. .env 또는 환경변수에 인증키를 설정하세요."
        )
    # data.go.kr exposes both encoded and decoded variants. requests performs
    # URL encoding, so normalize to the decoded form exactly once.
    return unquote(value)


class KcsClient:
    def __init__(self, api_key: str, *, timeout: float = 30.0, session: requests.Session | None = None):
        self._api_key = unquote(api_key.strip())
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers.update({"User-Agent": "personal-investment-dashboard/0.1"})
        if session is None:
            retry = Retry(
                total=5,
                connect=5,
                read=5,
                backoff_factor=1.0,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=("GET",),
            )
            adapter = HTTPAdapter(max_retries=retry)
            self._session.mount("https://", adapter)

    def fetch(self, endpoint: Endpoint, params: dict[str, str]) -> bytes:
        safe_params = {**params, "serviceKey": self._api_key}
        try:
            response = self._session.get(endpoint.url, params=safe_params, timeout=self._timeout)
        except requests.RequestException as exc:
            # requests exceptions can include the full URL (and key). Never echo it.
            raise RuntimeError(f"{endpoint.label} 요청에 실패했습니다: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise RuntimeError(f"{endpoint.label} 요청 HTTP 오류: {response.status_code}")
        return response.content


def collect_endpoint(
    client: KcsClient,
    endpoint: Endpoint,
    *,
    start_month: str,
    end_month: str,
    hs_codes: list[str],
    country_codes: list[str],
    output_root: Path,
) -> CollectionResult:
    base_params = {"strtYymm": start_month.replace("-", ""), "endYymm": end_month.replace("-", "")}
    request_params = _parameter_sets(endpoint, base_params, hs_codes, country_codes)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_dir = output_root / "raw" / endpoint.slug.replace("-", "_")
    csv_dir = output_root / "processed" / endpoint.slug.replace("-", "_")
    raw_dir.mkdir(parents=True, exist_ok=True)
    csv_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    raw_paths: list[Path] = []
    for index, params in enumerate(request_params, start=1):
        xml = client.fetch(endpoint, params)
        suffix = _safe_suffix(params)
        raw_path = raw_dir / f"{stamp}_{index:03d}_{suffix}.xml"
        raw_path.write_bytes(xml)
        raw_paths.append(raw_path)
        parsed = parse_xml(xml, endpoint)
        frame = parsed.frame.copy()
        for key in endpoint.dimensions:
            canonical = {"hsSgn": "requested_hs_code", "cntyCd": "requested_country_code"}[key]
            frame[canonical] = params[key]
        frames.append(frame)

    combined = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    csv_path = csv_dir / f"{stamp}_{start_month}_{end_month}.csv"
    combined.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return CollectionResult(
        endpoint=endpoint.slug,
        raw_paths=tuple(raw_paths),
        csv_path=csv_path,
        rows=len(combined),
        columns=tuple(combined.columns),
    )


def _parameter_sets(
    endpoint: Endpoint,
    base: dict[str, str],
    hs_codes: list[str],
    country_codes: list[str],
) -> list[dict[str, str]]:
    values = {"hsSgn": hs_codes, "cntyCd": [code.upper() for code in country_codes]}
    missing = [dim for dim in endpoint.dimensions if not values[dim]]
    if missing:
        names = ", ".join(missing)
        raise ConfigurationError(f"{endpoint.label}에 필요한 조회 조건이 없습니다: {names}")
    if not endpoint.dimensions:
        return [base.copy()]
    return [
        {**base, **dict(zip(endpoint.dimensions, combination, strict=True))}
        for combination in itertools.product(*(values[dim] for dim in endpoint.dimensions))
    ]


def _safe_suffix(params: dict[str, str]) -> str:
    parts = [params["strtYymm"], params["endYymm"]]
    parts.extend(params[key] for key in ("hsSgn", "cntyCd") if key in params)
    return "_".join(parts)
