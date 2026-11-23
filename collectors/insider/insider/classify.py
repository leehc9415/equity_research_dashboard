"""1층(노이즈 제거)·2층(방향) 분류 규칙.

각 거래 행에 아래 필드를 붙인다.
  event      BUY / SELL / GIFT_OUT / GIFT_IN / OPTION / PAID_IN / CONVERSION /
             INHERIT / COMP / LOAN / PLEDGE / MECHANICAL / STATUS / OTHER
  channel    장내 / 장외 / 시간외 (매수·매도일 때)
  direction  buy / sell / gift / caution / other / none
  view       insider(내부자 정제) / external(외부 5%·기관) / noise(거래 아님)
  why        view·direction 판정 근거(짧은 한국어)
dup(중복)은 여러 날을 모은 뒤 feed 단계에서 판정한다.
"""
import re

# (event, 키워드들) — 위에서부터 먼저 걸리는 규칙이 이긴다.
EVENT_RULES = [
    ("COMP", ["선택권부여", "부여취소", "선택권 부여", "부여(+)", "부여(-)", "소멸",
              "자사주 지급", "자기주식 지급", "자사주지급"]),
    ("MECHANICAL", ["신주인수권증서", "행사가액조정", "가액조정", "가액 조정", "조정"]),
    ("STATUS", ["오기", "주주명부", "단순 정정", "대표자변경", "대표자 변경", "기존 보유",
                "확인에 따른", "공동보유자"]),
    ("MECHANICAL", ["매수청구", "ETF", "BASKET", "자본감소"]),
    ("LOAN", ["환매조건부"]),
    ("GIFT_OUT", ["기부"]),
    ("BUY", ["대량매매(+)"]),
    ("SELL", ["대량매매(-)"]),
    ("OPTION", ["주식매수선택권", "스톡옵션", "stock option"]),
    ("CONVERSION", ["전환권", "신주인수권", "교환권", "전환청구", "권리행사", "출자전환",
                    "전환등", "CB", "BW", "EB", "사채",
                    "교환(-)", "교환(+)",
                    "전환(+)", "교환(+)", "전환사채", "상환전환"]),
    ("GIFT_OUT", ["증여(-)", "증여(－)"]),
    ("GIFT_IN", ["수증"]),
    ("INHERIT", ["상속"]),
    ("COMP", ["우리사주", "상여", "RSU", "PSU", "성과", "양도제한", "보상",
              "임직원 지급"]),
    ("PAID_IN", ["유상신주", "유상증자", "제3자배정", "실권주", "공모",
                 "신주인수(+)"]),
    ("LOAN", ["차입", "상환", "대여", "대차", "반환"]),
    ("PLEDGE", ["담보", "신탁계약"]),
    ("MECHANICAL", ["무상", "주식배당", "액면", "병합", "분할", "합병", "상장",
                    "감자", "주식교환", "주식이전", "소각", "전환상장"]),
    ("STATUS", ["신규선임", "신규보고", "퇴임", "특별관계", "특수관계인 추가",
                "특수관계인 제외", "편입", "제외", "신규", "보고자 변경",
                "계약 이전", "실제 매도한 것은 아님"]),
    ("BUY", ["장내매수", "장외매수", "시간외매매(+)", "시간외매수", "매수"]),
    ("SELL", ["장내매도", "장외매도", "시간외매매(-)", "시간외매도", "매도",
              "처분"]),
]

NOISE_EVENTS = {"INHERIT", "COMP", "LOAN", "PLEDGE", "MECHANICAL", "STATUS"}

# 기관·외부 투자자로 볼 보고자 이름
EXTERNAL_NAME = re.compile(
    r"자산운용|투자신탁|투자자문|창업투자|벤처투자|벤처캐피탈|인베스트먼트|"
    r"신기술사업투자|투자조합|사모투자|은행|보험|연금|공제회|"
    r"증권(주식회사|㈜|\(주\))?$|^(주식회사|㈜|\(주\))?.*증권$|"
    r"ASSET MANAGEMENT|SECURITIES|BANK|PENSION|INVESTMENT", re.I)


def classify_event(reason: str, note: str = None):
    r = (reason or "").strip()
    for ev, kws in EVENT_RULES:
        if any(k in r for k in kws):
            return ev
    # 사유가 '기타'면 비고 문구로 다시 판정
    n = (note or "").strip()
    if n:
        for ev, kws in EVENT_RULES:
            if ev in ("BUY", "SELL"):
                continue  # 비고의 '매수/매도' 단어만으로는 거래로 보지 않음
            if any(k in n for k in kws):
                return ev
    return "OTHER"


def channel_of(reason: str):
    r = reason or ""
    if "시간외" in r:
        return "시간외"
    if "대량매매" in r:
        return "대량매매"
    if "장외" in r:
        return "장외"
    if "장내" in r:
        return "장내"
    return None


def sign_of(reason: str, qty_change):
    r = reason or ""
    if "(+)" in r:
        return 1
    if "(-)" in r or "(－)" in r:
        return -1
    if isinstance(qty_change, (int, float)) and qty_change:
        return 1 if qty_change > 0 else -1
    return 0


INSIDER_REL = ("최대주주", "사실상지배", "임원", "계열회사")  # 주요주주(10%)는 보유목적으로 판단


def _n(s):
    return re.sub(r"\(.*?\)|주식회사|㈜|\s", "", s or "").upper()


def is_external_filer(form: str, filing: dict, corp: str = "") -> tuple:
    """(외부 여부, 근거). 거래 단위가 아니라 보고서(보고자) 단위 판정."""
    rep = filing.get("reporter") or {}
    name = rep.get("name") or ""
    if form == "BULK_S":
        return True, "약식 대량보유(단순·일반투자)"
    if form == "BULK_G":
        purpose = filing.get("purpose") or ""
        rel = rep.get("relation") or ""
        if any(k in rel for k in INSIDER_REL):
            return False, rel
        if purpose and "경영권" not in purpose:
            return True, f"보유목적 {purpose}"
        if "경영권" in purpose:
            return False, "보유목적 경영권 영향"
        return False, "대량보유(일반)"
    if form == "EXEC":
        if "우리사주" in name:
            return True, "우리사주조합"
        exec_reg = rep.get("exec_registered")
        if exec_reg in ("Y", "N"):
            return False, "임원"
        if rep.get("type_code") == "K" or EXTERNAL_NAME.search(name):
            same_group = _n(name)[:2] and _n(name)[:2] == _n(corp)[:2]
            if "사실상지배" not in (rep.get("relation") or "") and not same_group:
                return True, "기관·외부 주요주주"
        return False, rep.get("relation") or "주요주주"
    return False, "최대주주등"


def holder_group(relation: str, form: str, ext_why: str = "") -> str:
    """보고자 구분: 최대주주측 / 임원 / 주요주주 / 기타."""
    r = relation or ""
    if re.search(r"최대주주|사실상지배", r):
        return "최대주주측"
    if "임원" in r:
        return "임원"
    if re.search(r"친인척|본인|계열|특수관계|재단|공동보유", r):
        return "최대주주측"
    if re.search(r"10%|주요주주", r):
        return "주요주주"
    if form == "MAJOR" or (form == "BULK_G" and re.search(r"최대주주|사실상지배", ext_why or "")):
        return "최대주주측"
    return "기타"


def classify_tx(tx: dict, form: str, external: bool, ext_why: str) -> dict:
    ev = classify_event(tx.get("reason"), tx.get("note"))
    sign = sign_of(tx.get("reason"), tx.get("qty_change"))
    # 사유 글자와 수량 부호가 엇갈리면 부호를 따른다(예: 공개매수청약(-)은 매도)
    if ev == "BUY" and sign < 0:
        ev = "SELL"
    elif ev == "SELL" and sign > 0:
        ev = "BUY"
    tx["event"] = ev
    tx["sign"] = sign
    tx["channel"] = channel_of(tx.get("reason")) if ev in ("BUY", "SELL") else None

    if ev in ("BUY", "OPTION", "PAID_IN"):
        direction = "buy"
    elif ev == "SELL":
        direction = "sell"
    elif ev in ("GIFT_OUT", "GIFT_IN"):
        direction = "gift"
    elif ev == "CONVERSION":
        direction = "caution"
    elif ev == "OTHER":
        direction = "other"
    else:
        direction = "none"
    if tx.get("security_kind") == "other" and direction in ("buy", "sell"):
        direction = "caution"  # 전환사채 등 주식 외 증권 매매
    tx["holder_group"] = holder_group(tx.get("relation"), form, ext_why)
    # 최대주주 측이 사채(CB·BW·EB 인수·전환 등)로 지분을 늘린 경우는 매수로 본다
    if direction == "caution" and sign > 0 and tx["holder_group"] == "최대주주측":
        direction, tx["via"] = "buy", "사채 경유"
    tx["direction"] = direction

    if external:  # 외부 보고자는 사유와 관계없이 외부 목록으로(뷰어에서 보고서 단위로 묶임)
        view, why = "external", ext_why
    elif not tx.get("qty_change"):
        view, why = "noise", "수량 변동 없음"
    elif ev == "OPTION" and sign < 0:
        view, why = "noise", "행사로 줄어든 선택권 쪽 기록"
    elif ev in NOISE_EVENTS:
        view, why = "noise", {
            "INHERIT": "상속", "COMP": "보상·우리사주", "LOAN": "주식 대차·차입",
            "PLEDGE": "담보·신탁", "MECHANICAL": "무상·합병·상장 등 기계적 변동",
            "STATUS": "선임·퇴임·관계 변경",
        }[ev]
    else:
        view, why = "insider", ext_why
    tx["view"] = view
    tx["why"] = why
    # 신호 대시보드용 구분: 매매(스톡옵션 행사 제외) / 증여
    if view == "insider" and direction in ("buy", "sell") and ev != "OPTION":
        tx["signal"] = "trade"
    elif view == "insider" and direction == "gift":
        tx["signal"] = "gift"
    else:
        tx["signal"] = None
    price = tx.get("price")
    q = tx.get("qty_change")
    tx["amount"] = abs(q) * price if (price and q and ev not in NOISE_EVENTS) else None
    return tx
