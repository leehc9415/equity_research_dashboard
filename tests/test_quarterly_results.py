import sys, unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from investment_data.quarterly_results import amount, parse_rows, build_quarter, quarter_end
from update_quarterly_results import latest_period


def row(stock, fs, account, cur, prior, dt='2026.07.01 ~ 2026.09.30', sj='IS', rcept='20261114000123'):
    return dict(stock_code=stock, fs_div=fs, sj_div=sj, account_nm=account, thstrm_amount=cur, frmtrm_amount=prior, thstrm_dt=dt, rcept_no=rcept)


class QuarterlyResultsTest(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(amount('107,440,482,000,000'), 1074404.82)
        self.assertEqual(amount('-500,000,000'), -5.0)
        self.assertIsNone(amount('-'))
        self.assertIsNone(amount(''))

    def test_parse_prefers_consolidated_and_filters_other_fiscal_periods(self):
        rows = [
            row('005930', 'OFS', '영업이익', '1,000,000,000', '500,000,000'),
            row('005930', 'CFS', '영업이익', '3,000,000,000', '1,000,000,000'),
            row('005930', 'CFS', '매출액', '10,000,000,000', '-'),
            row('005930', 'CFS', '자산총계', '9', '9', sj='BS'),
            row('111111', 'OFS', '영업수익', '200,000,000', '100,000,000'),
            row('222222', 'CFS', '영업이익', '100,000,000', '1', dt='2026.04.01 ~ 2026.06.30'),
            dict(row('333333', 'CFS', '영업이익', '1,000', '1'), currency='USD'),
        ]
        parsed = parse_rows(rows, '2026.09.30')
        self.assertEqual(set(parsed), {'005930', '111111'})
        self.assertEqual(parsed['005930']['basis'], '연결')
        self.assertEqual(parsed['005930']['values']['operatingProfit'], (30.0, 10.0))
        self.assertEqual(parsed['005930']['values']['revenue'], (100.0, None))
        self.assertEqual(parsed['111111']['values']['revenue'], (2.0, 1.0))

    def test_build_joins_previous_quarter_only_on_same_basis_and_consensus(self):
        current = {'005930': {'basis': '연결', 'receipt': '20261114000123', 'values': {'operatingProfit': (30.0, 10.0)}},
                   '111111': {'basis': '별도', 'receipt': '20261113000001', 'values': {'operatingProfit': (5.0, 1.0)}}}
        previous = {'005930': {'basis': '연결', 'values': {'operatingProfit': (20.0, None)}},
                    '111111': {'basis': '연결', 'values': {'operatingProfit': (99.0, None)}}}
        snapshot = {'companies': [
            {'stockCode': '005930', 'fiscalMonth': 12, 'consensus': {'202609': {'revenue': None, 'operatingProfit': 25.0, 'netIncome': None}}, 'marketCap': 100.0},
            {'stockCode': '111111', 'fiscalMonth': 3, 'consensus': {'202609': {'operatingProfit': 4.0}}},
        ]}
        rows = {r['stock']: r for r in build_quarter('2026.3Q', current, previous, {'005930': ('00126380', '삼성전자')}, snapshot)}
        a, b = rows['005930'], rows['111111']
        self.assertEqual(a['company'], '삼성전자')
        self.assertEqual(a['filed'], '2026-11-14')
        self.assertEqual(a['metrics']['operatingProfit'], {'value': 30.0, 'priorYear': 10.0, 'previous': 20.0})
        self.assertEqual(a['consensus']['operatingProfit'], 25.0)
        self.assertIsNone(b['metrics']['operatingProfit']['previous'])  # 연결↔별도 비교 금지
        self.assertIsNone(b['consensus'])  # 3월 결산은 기간이 달라 컨센 미연결

    def test_periods(self):
        self.assertEqual(quarter_end('2026.3Q'), (2026, 3, '2026.09.30'))
        self.assertEqual(quarter_end('2026.1Q')[2], '2026.03.31')
        with self.assertRaises(ValueError):
            quarter_end('2026.4Q')
        self.assertEqual(latest_period(date(2026, 11, 26)), '2026.3Q')
        self.assertEqual(latest_period(date(2027, 1, 5)), '2026.3Q')
        self.assertEqual(latest_period(date(2027, 5, 1)), '2027.1Q')


if __name__ == '__main__':
    unittest.main()
