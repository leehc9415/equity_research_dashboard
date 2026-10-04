"""Lossless XML parsing followed by conservative column normalization."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .api_catalog import Endpoint


class ApiResponseError(RuntimeError):
    """Raised when the gateway returns a valid XML error response."""


@dataclass(frozen=True)
class ParsedResponse:
    frame: pd.DataFrame
    result_code: str | None
    result_message: str | None
    total_count: int | None
    page_no: int | None
    num_of_rows: int | None


def _text(root: ET.Element, tag: str) -> str | None:
    node = root.find(f".//{tag}")
    if node is None or node.text is None:
        return None
    return node.text.strip()


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def parse_xml(xml: str | bytes, endpoint: Endpoint) -> ParsedResponse:
    root = ET.fromstring(xml)
    result_code = _text(root, "resultCode")
    result_message = _text(root, "resultMsg")
    gateway_code = _text(root, "returnReasonCode")
    gateway_message = _text(root, "returnAuthMsg") or _text(root, "errMsg")
    if gateway_code or gateway_message:
        raise ApiResponseError(
            f"공공데이터포털 인증/게이트웨이 오류: "
            f"code={gateway_code or 'unknown'}, message={gateway_message or 'unknown'}"
        )
    if result_code not in (None, "00", "0", "NORMAL_SERVICE"):
        raise ApiResponseError(
            f"API 응답 오류: code={result_code}, message={result_message or 'unknown'}"
        )

    # Keep every field returned by the API. Canonical aliases are applied only
    # after extraction so new or undocumented fields remain visible.
    records: list[dict[str, str | None]] = []
    for item in root.findall(".//item"):
        records.append({child.tag: child.text.strip() if child.text else None for child in item})

    frame = pd.DataFrame.from_records(records)
    if not frame.empty:
        frame = frame.rename(columns=endpoint.aliases)
        frame = _normalize_frame(frame)

    return ParsedResponse(
        frame=frame,
        result_code=result_code,
        result_message=result_message,
        total_count=_integer(_text(root, "totalCount")),
        page_no=_integer(_text(root, "pageNo")),
        num_of_rows=_integer(_text(root, "numOfRows")),
    )


def parse_file(path: Path, endpoint: Endpoint) -> ParsedResponse:
    return parse_xml(path.read_bytes(), endpoint)


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if "date" in frame:
        cleaned = frame["date"].astype("string").str.replace(".", "-", regex=False)
        cleaned = cleaned.str.replace(r"^(\d{4})(\d{2})$", r"\1-\2", regex=True)
        frame["date"] = cleaned

    if "hs_code" in frame:
        # HS codes are identifiers. Keep leading zeroes and never coerce to int.
        frame["hs_code"] = frame["hs_code"].astype("string").str.strip()

    numeric_columns = {
        "export_value_usd",
        "export_weight_kg",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
        "export_count",
        "import_count",
    }
    for column in numeric_columns.intersection(frame.columns):
        values = frame[column].astype("string").str.replace(",", "", regex=False)
        frame[column] = pd.to_numeric(values, errors="coerce").astype("Int64")

    preferred = [
        "date",
        "hs_code",
        "product_name",
        "country_code",
        "country_name",
        "export_value_usd",
        "export_weight_kg",
        "import_value_usd",
        "import_weight_kg",
        "trade_balance_usd",
        "export_count",
        "import_count",
    ]
    ordered = [name for name in preferred if name in frame.columns]
    ordered.extend(name for name in frame.columns if name not in ordered)
    return frame.loc[:, ordered]


def profile_frame(frame: pd.DataFrame) -> dict[str, object]:
    hs_lengths: list[int] = []
    if "hs_code" in frame and not frame.empty:
        hs_lengths = sorted(
            {len(code) for code in frame["hs_code"].dropna().astype(str) if re.fullmatch(r"\d+", code)}
        )
    return {
        "rows": len(frame),
        "columns": list(frame.columns),
        "hs_code_lengths": hs_lengths,
        "null_counts": {name: int(count) for name, count in frame.isna().sum().items()},
    }
