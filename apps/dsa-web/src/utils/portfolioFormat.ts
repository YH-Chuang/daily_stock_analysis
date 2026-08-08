import type {
  PortfolioCashDirection,
  PortfolioCorporateActionType,
  PortfolioFxRefreshResponse,
  PortfolioImportCommitResponse,
  PortfolioImportParseResponse,
  PortfolioPositionItem,
  PortfolioSide,
} from '../types/portfolio';
import type { UiTextKey, UiTextParams } from '../i18n/uiText';
import { toDateInputValue } from './format';

/** 与 useUiLanguage().t 同签名，便于组件直接透传 t。 */
export type PortfolioTranslate = (key: UiTextKey, params?: UiTextParams) => string;

export type FxRefreshFeedback = {
  tone: 'neutral' | 'success' | 'warning';
  text: string;
};

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'danger';

export function getTodayIso(): string {
  return toDateInputValue(new Date());
}

export function formatMoney(value: number | undefined | null, currency = 'CNY'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${value.toFixed(2)}%`;
}

export function formatSignedPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function hasPositionPrice(row: PortfolioPositionItem): boolean {
  return row.priceAvailable !== false && row.priceSource !== 'missing';
}

export function formatPositionPrice(row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return row.lastPrice.toFixed(4);
}

export function formatPositionMoney(value: number, row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return formatMoney(value, row.valuationCurrency);
}

export function getPositionPriceLabel(row: PortfolioPositionItem, t: PortfolioTranslate): string {
  if (!hasPositionPrice(row)) return t('portfolio.priceMissing');
  if (row.priceSource === 'realtime_quote') {
    return row.priceProvider
      ? t('portfolio.priceRealtimeWithProvider', { provider: row.priceProvider })
      : t('portfolio.priceRealtime');
  }
  if (row.priceSource === 'history_close') {
    return row.priceStale && row.priceDate
      ? t('portfolio.priceCloseWithDate', { date: row.priceDate })
      : t('portfolio.priceClose');
  }
  // priceSource 为后端返回的原始来源标识，保持原样不翻译。
  return row.priceSource || t('portfolio.priceUnknownSource');
}

export function formatSideLabel(value: PortfolioSide, t: PortfolioTranslate): string {
  return value === 'buy' ? t('portfolio.buy') : t('portfolio.sell');
}

export function formatCashDirectionLabel(value: PortfolioCashDirection, t: PortfolioTranslate): string {
  return value === 'in' ? t('portfolio.cashIn') : t('portfolio.cashOut');
}

export function formatCorporateActionLabel(
  value: PortfolioCorporateActionType,
  t: PortfolioTranslate,
): string {
  return value === 'cash_dividend'
    ? t('portfolio.cashDividend')
    : t('portfolio.splitAdjustment');
}

export function formatBrokerLabel(value: string, t: PortfolioTranslate, displayName?: string): string {
  // displayName 来自后端账户配置，保持原样不翻译。
  if (displayName && displayName.trim()) {
    return t('portfolio.brokerWithName', { broker: value, name: displayName.trim() });
  }
  if (value === 'huatai') {
    return t('portfolio.brokerWithName', { broker: value, name: t('portfolio.brokerHuatai') });
  }
  if (value === 'citic') {
    return t('portfolio.brokerWithName', { broker: value, name: t('portfolio.brokerCitic') });
  }
  if (value === 'cmb') {
    return t('portfolio.brokerWithName', { broker: value, name: t('portfolio.brokerCmb') });
  }
  return value;
}

export function buildFxRefreshFeedback(
  data: PortfolioFxRefreshResponse,
  t: PortfolioTranslate,
): FxRefreshFeedback {
  if (data.refreshEnabled === false) {
    return {
      tone: 'neutral',
      text: t('portfolio.fxRefreshDisabled'),
    };
  }

  if (data.pairCount === 0) {
    return {
      tone: 'neutral',
      text: t('portfolio.fxRefreshNoPairs'),
    };
  }

  if (data.updatedCount > 0 && data.staleCount === 0 && data.errorCount === 0) {
    return {
      tone: 'success',
      text: t('portfolio.fxRefreshSuccess', { count: data.updatedCount }),
    };
  }

  const summary = t('portfolio.fxRefreshSummary', {
    updated: data.updatedCount,
    stale: data.staleCount,
    failed: data.errorCount,
  });
  if (data.staleCount > 0) {
    return {
      tone: 'warning',
      text: t('portfolio.fxRefreshStale', { summary }),
    };
  }

  return {
    tone: 'warning',
    text: t('portfolio.fxRefreshPartial', { summary }),
  };
}

export function getFxRefreshFeedbackVariant(tone: FxRefreshFeedback['tone']): PortfolioAlertVariant {
  if (tone === 'success') return 'success';
  if (tone === 'warning') return 'warning';
  return 'info';
}

export function getCsvParseVariant(result: PortfolioImportParseResponse): PortfolioAlertVariant {
  return result.errorCount > 0 || result.skippedCount > 0 ? 'warning' : 'info';
}

export function getCsvCommitVariant(result: PortfolioImportCommitResponse, isDryRun: boolean): PortfolioAlertVariant {
  if (isDryRun) return 'info';
  return result.failedCount > 0 || result.duplicateCount > 0 ? 'warning' : 'success';
}
