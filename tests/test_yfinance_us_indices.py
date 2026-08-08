# -*- coding: utf-8 -*-
"""
data_provider/yfinance_fetcher 中美股指数获取逻辑的单元测试

使用 unittest.mock 模拟 yfinance API 响应，覆盖：
- _fetch_yf_batch_data 批量指数数据解析（单代码扁平列 / 多代码 MultiIndex）
- _get_us_main_indices 美股指数批量获取及异常场景
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import pandas as pd

# 在导入 data_provider 前 mock 可能缺失的依赖，避免环境差异导致测试无法运行
if 'fake_useragent' not in sys.modules:
    sys.modules['fake_useragent'] = MagicMock()

# 确保能导入项目模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def _make_mock_hist(close: float, prev_close: float, high: float = None, low: float = None) -> pd.DataFrame:
    """构造模拟的 history DataFrame，包含计算涨跌幅所需字段"""
    high = high if high is not None else close + 1
    low = low if low is not None else close - 1
    return pd.DataFrame({
        'Close': [prev_close, close],
        'Open': [prev_close - 0.5, close - 0.3],
        'High': [prev_close + 1, high],
        'Low': [prev_close - 1, low],
        'Volume': [1000000.0, 1200000.0],
    }, index=pd.DatetimeIndex(['2025-02-16', '2025-02-17']))


def _make_mock_yf(hist_df: pd.DataFrame):
    """构造模拟的 yf 模块，download() 对所有请求代码返回同一份 DataFrame（MultiIndex 列）"""
    def download(tickers, **kwargs):
        if hist_df.empty or not list(tickers):
            return pd.DataFrame()
        return pd.concat({symbol: hist_df for symbol in tickers}, axis=1)

    mock_yf = MagicMock()
    mock_yf.download.side_effect = download
    return mock_yf


def _make_mock_yf_per_symbol(frames):
    """构造模拟的 yf 模块，download() 只返回 frames 中存在的代码（模拟部分代码无数据）"""
    def download(tickers, **kwargs):
        available = {symbol: frames[symbol] for symbol in tickers if symbol in frames}
        if not available:
            return pd.DataFrame()
        return pd.concat(available, axis=1)

    mock_yf = MagicMock()
    mock_yf.download.side_effect = download
    return mock_yf


def _downloaded_tickers(mock_yf) -> list:
    """收集 yf.download() 收到的全部代码"""
    tickers = []
    for call in mock_yf.download.call_args_list:
        value = call.kwargs.get('tickers', call.args[0] if call.args else [])
        tickers.extend(value)
    return tickers


class TestFetchYfBatchData(unittest.TestCase):
    """_fetch_yf_batch_data 批量取数逻辑测试"""

    def setUp(self):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        self.fetcher = YfinanceFetcher()

    def test_returns_dict_with_correct_fields(self):
        """正常数据应返回包含 code/name/current/change_pct 等字段的字典"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = _make_mock_yf(mock_hist)

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result['code'], 'SPX')
        self.assertEqual(result['name'], '标普500指数')
        self.assertEqual(result['current'], 5100.0)
        self.assertEqual(result['prev_close'], 5000.0)
        self.assertEqual(result['change'], 100.0)
        self.assertAlmostEqual(result['change_pct'], 2.0)
        self.assertIn('open', result)
        self.assertIn('high', result)
        self.assertIn('low', result)
        self.assertIn('volume', result)
        self.assertIn('amount', result)
        self.assertIn('amplitude', result)

    def test_returns_empty_when_download_empty(self):
        """download 返回空表时应返回空列表"""
        mock_yf = _make_mock_yf(pd.DataFrame())

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(results, [])

    def test_single_row_history_uses_same_as_prev(self):
        """仅一行数据时 prev_close 等于 current，change_pct 为 0"""
        mock_hist = _make_mock_hist(close=5000.0, prev_close=5000.0).iloc[[-1]]
        mock_yf = _make_mock_yf(mock_hist)

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['change_pct'], 0.0)
        self.assertEqual(results[0]['prev_close'], results[0]['current'])

    def test_handles_flat_columns_for_single_symbol(self):
        """单个代码时 yf.download 返回扁平列，也应能正确解析"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = MagicMock()
        mock_yf.download.return_value = mock_hist

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['code'], 'SPX')
        self.assertEqual(results[0]['current'], 5100.0)
        self.assertAlmostEqual(results[0]['change_pct'], 2.0)

    def test_handles_field_ticker_level_order(self):
        """MultiIndex 层级为 (字段, 代码) 时同样应能取到数据"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        swapped = pd.concat({'^GSPC': mock_hist}, axis=1).swaplevel(0, 1, axis=1)
        mock_yf = MagicMock()
        mock_yf.download.return_value = swapped

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['current'], 5100.0)

    def test_skips_symbol_without_data_and_keeps_others(self):
        """单个代码无数据时只跳过该代码，其余代码照常返回（fail-open）"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = _make_mock_yf_per_symbol({'^GSPC': mock_hist})

        results = self.fetcher._fetch_yf_batch_data(
            mock_yf,
            {'SPX': ('^GSPC', '标普500指数'), 'IXIC': ('^IXIC', '纳斯达克综合指数')},
        )

        self.assertEqual([item['code'] for item in results], ['SPX'])

    def test_skips_rows_with_nan_close(self):
        """批量下载中缺失交易日补的 NaN 行应被剔除，不参与涨跌幅计算"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        padded = pd.concat([
            mock_hist,
            pd.DataFrame(
                {col: [float('nan')] for col in mock_hist.columns},
                index=pd.DatetimeIndex(['2025-02-18']),
            ),
        ])
        mock_yf = _make_mock_yf(padded)

        results = self.fetcher._fetch_yf_batch_data(mock_yf, {'SPX': ('^GSPC', '标普500指数')})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['current'], 5100.0)
        self.assertEqual(results[0]['prev_close'], 5000.0)

    def test_issues_single_download_call_for_all_symbols(self):
        """批量取数应只发起一次 download 请求，降低 Yahoo 限流风险"""
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = _make_mock_yf(mock_hist)

        self.fetcher._fetch_yf_batch_data(
            mock_yf,
            {'SPX': ('^GSPC', '标普500指数'), 'IXIC': ('^IXIC', '纳斯达克综合指数')},
        )

        self.assertEqual(mock_yf.download.call_count, 1)
        self.assertEqual(_downloaded_tickers(mock_yf), ['^GSPC', '^IXIC'])
        self.assertEqual(mock_yf.download.call_args.kwargs['period'], '2d')


class TestGetUsMainIndices(unittest.TestCase):
    """_get_us_main_indices 美股指数批量获取测试"""

    def setUp(self):
        from data_provider.yfinance_fetcher import YfinanceFetcher
        self.fetcher = YfinanceFetcher()

    @patch('data_provider.yfinance_fetcher.get_us_index_yf_symbol')
    def test_returns_list_when_mock_succeeds(self, mock_get_symbol):
        """当映射与取数均成功时返回指数列表"""
        def get_symbol(code):
            mapping = {
                'SPX': ('^GSPC', '标普500指数'),
                'IXIC': ('^IXIC', '纳斯达克综合指数'),
                'DJI': ('^DJI', '道琼斯工业指数'),
                'VIX': ('^VIX', 'VIX恐慌指数'),
            }
            return mapping.get(code, (None, None))

        mock_get_symbol.side_effect = get_symbol
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = _make_mock_yf(mock_hist)

        result = self.fetcher._get_us_main_indices(mock_yf)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertGreaterEqual(len(result), 1)
        for item in result:
            self.assertIn('code', item)
            self.assertIn('name', item)
            self.assertIn('current', item)
            self.assertIn('change_pct', item)

    @patch('data_provider.yfinance_fetcher.get_us_index_yf_symbol')
    def test_handles_empty_history_gracefully(self, mock_get_symbol):
        """部分指数无数据时仍返回能取到数据的指数"""
        def get_symbol(code):
            return ('^GSPC', '标普500指数') if code == 'SPX' else (
                ('^IXIC', '纳斯达克综合指数') if code == 'IXIC' else (None, None)
            )

        mock_get_symbol.side_effect = get_symbol
        mock_yf = _make_mock_yf_per_symbol({'^GSPC': _make_mock_hist(close=5100.0, prev_close=5000.0)})

        result = self.fetcher._get_us_main_indices(mock_yf)

        self.assertIsNotNone(result)
        self.assertIsInstance(result, list)
        self.assertEqual([item['code'] for item in result], ['SPX'])

    @patch('data_provider.yfinance_fetcher.get_us_index_yf_symbol')
    def test_returns_none_when_all_fail(self, mock_get_symbol):
        """全部取数失败时返回 None"""
        mock_get_symbol.return_value = (None, None)
        mock_yf = _make_mock_yf(pd.DataFrame())

        result = self.fetcher._get_us_main_indices(mock_yf)

        self.assertIsNone(result)

    @patch('data_provider.yfinance_fetcher.get_us_index_yf_symbol')
    def test_handles_download_exception(self, mock_get_symbol):
        """yf.download 抛异常时降级返回 None，不向上抛出"""
        mock_get_symbol.return_value = ('^GSPC', '标普500指数')
        mock_yf = MagicMock()
        mock_yf.download.side_effect = Exception("Network error")

        result = self.fetcher._get_us_main_indices(mock_yf)

        self.assertIsNone(result)

    @patch('data_provider.yfinance_fetcher.get_us_index_yf_symbol')
    def test_skips_unknown_index_code(self, mock_get_symbol):
        """get_us_index_yf_symbol 返回 (None, None) 的代码应被跳过"""
        def get_symbol(code):
            if code == 'SPX':
                return ('^GSPC', '标普500指数')
            return (None, None)

        mock_get_symbol.side_effect = get_symbol
        mock_hist = _make_mock_hist(close=5100.0, prev_close=5000.0)
        mock_yf = _make_mock_yf(mock_hist)

        result = self.fetcher._get_us_main_indices(mock_yf)

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['code'], 'SPX')


if __name__ == '__main__':
    unittest.main()
