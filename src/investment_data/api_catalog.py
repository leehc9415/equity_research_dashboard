"""Korea Customs Service API endpoint and field metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Endpoint:
    slug: str
    label: str
    url: str
    dimensions: tuple[str, ...]
    aliases: dict[str, str]


COMMON_ALIASES = {
    "year": "date",
    "hsCd": "hs_code",
    "hsCode": "hs_code",
    "statKor": "product_name",
    "statCd": "country_code",
    "statCdCntnKor1": "country_name",
    "expDlr": "export_value_usd",
    "expWgt": "export_weight_kg",
    "impDlr": "import_value_usd",
    "impWgt": "import_weight_kg",
    "balPayments": "trade_balance_usd",
    "expCnt": "export_count",
    "impCnt": "import_count",
}


ENDPOINTS: dict[str, Endpoint] = {
    "trade-summary": Endpoint(
        slug="trade-summary",
        label="수출입총괄",
        url="https://apis.data.go.kr/1220000/Newtrade/getNewtradeList",
        dimensions=(),
        aliases=COMMON_ALIASES,
    ),
    "item-trade": Endpoint(
        slug="item-trade",
        label="품목별 수출입실적",
        url="https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList",
        dimensions=("hsSgn",),
        aliases=COMMON_ALIASES,
    ),
    "item-country-trade": Endpoint(
        slug="item-country-trade",
        label="품목별 국가별 수출입실적",
        url="https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList",
        dimensions=("hsSgn", "cntyCd"),
        aliases=COMMON_ALIASES,
    ),
    "country-trade": Endpoint(
        slug="country-trade",
        label="국가별 수출입실적",
        url="https://apis.data.go.kr/1220000/nationtrade/getNationtradeList",
        dimensions=("cntyCd",),
        aliases=COMMON_ALIASES,
    ),
}
