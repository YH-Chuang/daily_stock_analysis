import { describe, expect, it } from 'vitest';

import { getReportText, normalizeReportLanguage } from '../reportLanguage';
import { getSentimentLabel } from '../../types/analysis';

describe('reportLanguage ko support', () => {
  it('normalizes ko and falls back to zh for unknown', () => {
    expect(normalizeReportLanguage('ko')).toBe('ko');
    expect(normalizeReportLanguage('en')).toBe('en');
    expect(normalizeReportLanguage('fr')).toBe('zh');
    expect(normalizeReportLanguage(undefined)).toBe('zh');
  });

  it('returns Korean report copy for ko', () => {
    const ko = getReportText('ko');
    expect(ko.keyInsights).toBe('핵심 인사이트');
    expect(ko.actionAdvice).toBe('대응 전략');
    expect(ko.fullReport).toBe('전체 분석 리포트');
  });

  it('keeps zh/en report copy unchanged', () => {
    expect(getReportText('zh').keyInsights).toBe('核心洞察');
    expect(getReportText('en').keyInsights).toBe('KEY INSIGHTS');
  });

  it('normalizes the zh-tw aliases the backend can emit', () => {
    expect(normalizeReportLanguage('zh-tw')).toBe('zh-tw');
    expect(normalizeReportLanguage('zh-TW')).toBe('zh-tw');
    expect(normalizeReportLanguage('zh_tw')).toBe('zh-tw');
  });

  it('returns Traditional Chinese report copy with Taiwan wording for zh-tw', () => {
    const tw = getReportText('zh-tw');
    expect(tw.actionAdvice).toBe('操作建議');
    expect(tw.idealBuy).toBe('理想買進');
    expect(tw.stopLoss).toBe('停損價位');
    expect(tw.boardLinkage).toBe('類股連動');
    // 不得留下简体字面值，否则繁体正文上方会出现简体界面文案。
    for (const value of Object.values(tw)) {
      expect(value).not.toMatch(/[买卖损板块仓资讯据线]/);
    }
  });

  it('returns Traditional sentiment labels for zh-tw', () => {
    expect(getSentimentLabel(90, 'zh-tw')).toBe('極度樂觀');
    expect(getSentimentLabel(70, 'zh-tw')).toBe('樂觀');
    expect(getSentimentLabel(50, 'zh-tw')).toBe('中性');
    expect(getSentimentLabel(10, 'zh-tw')).toBe('極度悲觀');
    // zh 行為不變。
    expect(getSentimentLabel(70, 'zh')).toBe('乐观');
  });

  it('returns Korean sentiment labels by band', () => {
    expect(getSentimentLabel(90, 'ko')).toBe('매우 낙관');
    expect(getSentimentLabel(50, 'ko')).toBe('중립');
    expect(getSentimentLabel(10, 'ko')).toBe('매우 비관');
    expect(getSentimentLabel(90, 'en')).toBe('Very Bullish');
  });
});
