"""실제 공시 원문(tests/fixtures)으로 파서·분류 규칙을 검사한다.

  python -m unittest discover tests
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from insider.classify import classify_event  # noqa: E402
from insider.feed import dedup, mark_intra_group, mark_transfers, pair_gifts  # noqa: E402
from insider.parsers import form_of, parse  # noqa: E402
from insider.pipeline import build_filing  # noqa: E402

FX = ROOT / "tests" / "fixtures"


def load(rcept_no):
    meta = json.loads((FX / f"{rcept_no}.meta.json").read_text(encoding="utf-8"))
    form = form_of(meta["report_nm"])
    parsed = parse(form, (FX / f"{rcept_no}.xml").read_bytes())
    return build_filing(meta, form, parsed)


class Exec(unittest.TestCase):
    def test_samsung_open_market_buy(self):
        f, txs, _ = load("20261106000410")  # 삼성전자 노태문 장내매수
        self.assertEqual(f["form"], "EXEC")
        self.assertFalse(f["external"])
        (t,) = txs
        self.assertEqual(t["person"], "노태문")
        self.assertEqual(t["event"], "BUY")
        self.assertEqual(t["view"], "insider")
        self.assertEqual(t["qty_change"], 3000)
        self.assertEqual(t["price"], 210000)
        self.assertEqual(t["amount"], 630_000_000)
        self.assertEqual(t["relation"], "등기임원")
        self.assertEqual(t["date"], "2026-11-06")


class Bulk(unittest.TestCase):
    def test_gift_pair_and_funding(self):
        f, txs, _ = load("20261106000450")  # 한국피아이엠 송영우→김현지 증여
        ev = {t["person"]: t["event"] for t in txs}
        self.assertEqual(ev["송영우"], "GIFT_OUT")
        self.assertEqual(ev["김현지"], "GIFT_IN")
        self.assertTrue(all(t["view"] == "insider" for t in txs))
        pair_gifts(txs)
        giver = next(t for t in txs if t["person"] == "송영우")
        taker = next(t for t in txs if t["person"] == "김현지")
        self.assertEqual(giver["counterparty"], "김현지")
        self.assertEqual(giver["view"], "insider")
        self.assertEqual((taker["view"], taker["dup_of"]), ("dup", giver["id"]))
        self.assertTrue(any("증여" in (x.get("detail") or "") for x in f["funding"]))

    def test_simple_form_is_external(self):
        f, txs, _ = load("20261106000307")  # 브이아이피자산운용 약식
        self.assertTrue(f["external"])
        self.assertTrue(txs)
        self.assertTrue(all(t["view"] in ("external", "noise") for t in txs))
        self.assertEqual(txs[0]["price"], 15534)


    def test_one_gift_to_several_recipients(self):
        base = dict(stock_code="000001", date="2026-11-01", view="insider")
        o = dict(base, id="o", event="GIFT_OUT", person="부", qty_change=-300)
        a = dict(base, id="a", event="GIFT_IN", person="자1", qty_change=100)
        b = dict(base, id="b", event="GIFT_IN", person="자2", qty_change=200)
        pair_gifts([o, a, b])
        self.assertEqual(o["counterparty"], "자1, 자2")
        self.assertEqual(len(o["recipients"]), 2)
        self.assertEqual((a["view"], b["view"]), ("dup", "dup"))


    def test_intra_group_transfer(self):
        base = dict(stock_code="004080", corp_name="신흥", date="2026-07-28", view="insider",
                    signal="trade", channel="시간외", price=14040, rcept_no="20261106000083")
        s = dict(base, id="s", person="신성치재", qty_change=-2240)
        b1 = dict(base, id="b1", person="이재민", qty_change=1166)
        b2 = dict(base, id="b2", person="이상민", qty_change=1074)
        mkt = dict(base, id="m", person="김씨", qty_change=500, channel="장내")
        mark_intra_group([s, b1, b2, mkt])
        self.assertTrue(s["intra"] and b1["intra"] and b2["intra"])
        self.assertIsNone(s["signal"])
        self.assertEqual(mkt["signal"], "trade")  # 장내 매수는 그대로

    def test_big_transfer_goes_to_gift_screen(self):
        base = dict(stock_code="004080", corp_name="신흥", date="2026-08-26", view="insider",
                    signal="trade", channel="대량매매", price=14050, rcept_no="1", shares=9_200_000)
        s = dict(base, id="s", person="이용익", person_id="560529", qty_change=-100_000, qty_before=1_685_775)
        b = dict(base, id="b", person="디브이홀딩스", person_type="국내법인", qty_change=100_000, qty_before=846_143)
        small_s = dict(base, id="s2", person="송광자", person_id="400101", qty_change=-50, qty_before=100, channel="시간외")
        small_b = dict(base, id="b2", person="조현준", person_id="680101", qty_change=50, qty_before=100, channel="시간외")
        txs = [s, b, small_s, small_b]
        mark_intra_group(txs)
        self.assertEqual(mark_transfers(txs), 1)
        self.assertTrue(s["transfer"])
        self.assertEqual(s["signal"], "gift")
        self.assertEqual(s["recipients"][0]["person"], "디브이홀딩스")
        self.assertIsNone(small_s["signal"])

    def test_control_sale_is_not_intra_group(self):
        base = dict(stock_code="089860", corp_name="롯데렌탈", date="2026-10-30", view="insider",
                    signal="trade", channel="장외", price=59000, rcept_no="1", shares=36_000_000)
        s = dict(base, id="s", person="호텔롯데", qty_change=-22_212_063, qty_before=22_212_063)
        b = dict(base, id="b", person="인수자", qty_change=22_212_063, qty_before=0)
        mark_intra_group([s, b])
        self.assertEqual((s["signal"], b["signal"]), ("trade", "trade"))


    def test_dedup_contract_vs_settlement_date(self):
        base = dict(stock_code="004080", corp_name="신흥", person="코메트", event="SELL", view="insider", qty_change=-4200)
        a = dict(base, id="a", rcept_no="20261104800001", form="MAJOR", form_label="최대주주등", date="2026-10-30")
        b = dict(base, id="b", rcept_no="20261106000083", form="BULK_G", form_label="대량보유", date="2026-10-28", price=14338)
        c = dict(base, id="c", rcept_no="20261106000083", form="BULK_G", form_label="대량보유", date="2026-10-20", price=14000)
        dedup([a, b, c])
        self.assertEqual(a["view"], "dup")      # 결제일 쪽(최대주주변동)이 계약일 쪽(대량보유)에 합쳐짐
        self.assertEqual(b["view"], "insider")
        self.assertEqual(c["view"], "insider")  # 날짜가 멀면 별개 거래


    def test_dedup_big_trade_with_different_names(self):
        base = dict(stock_code="900270", corp_name="헝셩그룹", event="PAID_IN", view="insider",
                    qty_change=13_000_000, shares=32_700_000, date="2026-10-09")
        a = dict(base, id="a", rcept_no="20261015000385", form="EXEC", form_label="임원", person="Hui Mei Nga")
        b = dict(base, id="b", rcept_no="20261015000371", form="EXEC", form_label="임원", person="후이메이야")
        dedup([a, b])
        self.assertEqual(sorted([a["view"], b["view"]]), ["dup", "insider"])


class Plan(unittest.TestCase):
    def test_trading_plan(self):
        f, txs, plans = load("20261105000427")  # LSK아이로봇 CB 장외매도 계획
        self.assertEqual(f["form"], "PLAN")
        (p,) = plans
        self.assertEqual(p["method"], "장외매도(-)")
        self.assertEqual(p["amount"], 2_000_000_000)
        self.assertEqual(p["start"], "2026-12-07")
        self.assertEqual(p["sign"], -1)


class Major(unittest.TestCase):
    def test_hd_marine(self):
        f, txs, _ = load("20261106800193")
        self.assertEqual(f["shares"], 44_833_460)
        (t,) = txs
        self.assertEqual((t["person"], t["event"], t["qty_change"]), ("김정혁", "BUY", 200))

    def test_gift_with_note(self):
        f, txs, _ = load("20261105800956")  # 수산인더스트리 정석현 증여
        gifts = [t for t in txs if t["event"] == "GIFT_OUT"]
        self.assertEqual(len(gifts), 2)
        pair_gifts(txs)
        self.assertTrue(any(t.get("counterparty") == "정보윤" for t in gifts))

    def test_nested_tables_not_double_counted(self):
        f, txs, _ = load("20261105800559")  # 한화머시너리앤서비스홀딩스 공개매수
        self.assertEqual(len(txs), 3)
        ev = {t["person"]: t["event"] for t in txs}
        self.assertEqual(ev["김동선"], "BUY")   # 공개매수(+)
        self.assertEqual(ev["김동관"], "SELL")  # 공개매수청약(-)

    def test_status_change_is_noise(self):
        f, txs, _ = load("20261106800802")  # 롯데지주: 특수관계인 추가·퇴임 제외
        noise = [t for t in txs if t["view"] == "noise"]
        self.assertTrue(any("퇴임" in (t["note"] or "") for t in noise))
        self.assertTrue(any(t["event"] == "BUY" and t["view"] == "insider" for t in txs))


class Rules(unittest.TestCase):
    def test_event_rules(self):
        cases = {
            "장내매수(+)": "BUY", "장외매도(-)": "SELL", "시간외매매(+)": "BUY",
            "증여(-)": "GIFT_OUT", "수증(+)": "GIFT_IN", "신규선임(+)": "STATUS",
            "무상신주취득(+)": "MECHANICAL", "신규상장(무상취득)(+)": "MECHANICAL",
            "주식매수선택권행사(+)": "OPTION", "전환권등행사(+)": "CONVERSION",
            "차입(+)": "LOAN", "상속(+)": "INHERIT", "유상신주취득(+)": "PAID_IN",
        }
        for r, ev in cases.items():
            self.assertEqual(classify_event(r), ev, r)
        self.assertEqual(classify_event("기타(+)", "우리사주조합계정 대체입고"), "COMP")
        self.assertEqual(classify_event("기타(-)", "등기임원 퇴임으로 특수관계인 제외"), "STATUS")

    def test_dedup_prefers_exec_report(self):
        base = dict(stock_code="000001", corp_name="가", person="홍길동", date="2026-11-01",
                    event="BUY", qty_change=100, view="insider")
        a = dict(base, id="1-0", rcept_no="20261102800001", form="MAJOR", form_label="최대주주등", price=None)
        b = dict(base, id="2-0", rcept_no="20261102000002", form="EXEC", form_label="임원", price=5000, amount=500000)
        n = dedup([a, b])
        self.assertEqual(n, 1)
        self.assertEqual(a["view"], "dup")
        self.assertEqual(a["dup_of"], "2-0")
        self.assertEqual(b["view"], "insider")


if __name__ == "__main__":
    unittest.main()
