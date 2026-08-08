import { describe, expect, it } from 'vitest';
import {
  buildFxRefreshFeedback,
  formatBrokerLabel,
  formatMoney,
  formatPositionMoney,
  formatPositionPrice,
  formatSignedPct,
  getCsvCommitVariant,
  getCsvParseVariant,
  getPositionPriceLabel,
} from '../portfolioFormat';
import type { PortfolioPositionItem } from '../../types/portfolio';
import { formatUiText, UI_TEXT, type UiTextKey } from '../../i18n/uiText';

// 这些格式化函数改为接收 t，测试仍走真实的简体文案表，不用假字典 —— 否则断言
// 只证明 mock 自己一致，产品文案改错也不会失败。
const t = (key: UiTextKey, params?: Record<string, string | number>) =>
  formatUiText(UI_TEXT.zh[key], params);

const pricedPosition: PortfolioPositionItem = {
  symbol: 'HK00700',
  market: 'hk',
  currency: 'HKD',
  quantity: 100,
  avgCost: 300,
  totalCost: 30000,
  lastPrice: 321.12345,
  marketValueBase: 32112.345,
  unrealizedPnlBase: 2112.345,
  unrealizedPnlPct: 7.04,
  valuationCurrency: 'CNY',
  priceSource: 'realtime_quote',
  priceProvider: 'longbridge',
  priceAvailable: true,
};

describe('portfolioFormat', () => {
  it('formats money and signed percentages consistently', () => {
    expect(formatMoney(1234.5, 'USD')).toBe('USD 1,234.50');
    expect(formatMoney(null)).toBe('--');
    expect(formatSignedPct(3.456)).toBe('+3.46%');
    expect(formatSignedPct(-1.2)).toBe('-1.20%');
  });

  it('formats position price fields based on price availability', () => {
    expect(formatPositionPrice(pricedPosition)).toBe('321.1234');
    expect(formatPositionMoney(123, pricedPosition)).toBe('CNY 123.00');
    expect(getPositionPriceLabel(pricedPosition, t)).toBe('实时价 · longbridge');

    const missingPosition = { ...pricedPosition, priceAvailable: false, priceSource: 'missing' };
    expect(formatPositionPrice(missingPosition)).toBe('--');
    expect(formatPositionMoney(123, missingPosition)).toBe('--');
    expect(getPositionPriceLabel(missingPosition, t)).toBe('缺价');
  });

  it('formats broker labels and CSV result variants', () => {
    expect(formatBrokerLabel('huatai', t)).toBe('huatai（华泰）');
    expect(formatBrokerLabel('custom', t, ' 自定义 ')).toBe('custom（自定义）');
    expect(getCsvParseVariant({ broker: 'huatai', recordCount: 1, skippedCount: 1, errorCount: 0, records: [], errors: [] })).toBe('warning');
    expect(getCsvCommitVariant({ accountId: 1, recordCount: 1, insertedCount: 1, duplicateCount: 0, failedCount: 0, dryRun: false, errors: [] }, false)).toBe('success');
  });

  it('builds FX refresh feedback from refresh outcomes', () => {
    expect(buildFxRefreshFeedback({
      asOf: '2026-03-19',
      accountCount: 1,
      refreshEnabled: false,
      disabledReason: 'disabled',
      pairCount: 1,
      updatedCount: 0,
      staleCount: 0,
      errorCount: 0,
    }, t)).toMatchObject({ tone: 'neutral' });

    expect(buildFxRefreshFeedback({
      asOf: '2026-03-19',
      accountCount: 1,
      refreshEnabled: true,
      disabledReason: null,
      pairCount: 1,
      updatedCount: 1,
      staleCount: 0,
      errorCount: 0,
    }, t)).toMatchObject({ tone: 'success' });
  });
});
