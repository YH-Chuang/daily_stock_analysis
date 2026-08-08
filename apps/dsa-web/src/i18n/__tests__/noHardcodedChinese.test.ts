import { describe, expect, it } from 'vitest';

// 用 Vite 的 glob import 取原始码，避免依赖 node:fs（本项目的 tsconfig 不含 Node 型别）。
const SOURCES = import.meta.glob('../../{pages,components,utils,api}/**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/**
 * 防止「写死的中文字串」重新长回来。
 *
 * 背景：/chat、/portfolio、/alerts 曾经有大量写死的简体中文，不随界面语言切换 ——
 * 把界面切成英文，画面上仍然是简体。判据是英文界面下页面文本仍含中文，说明那些字串
 * 从未进入 i18n 体系。
 *
 * 这个测试对已完成改造的文件采用**白名单**：允许存在的中文必须逐条列出并写明理由。
 * 新增任何未登记的中文字面值都会让测试失败，所以下次有人图省事写死文案时会被挡住。
 */

const CJK = /[一-鿿]/;

/**
 * 允许保留中文的行。key 是文件，value 是「该行必须包含的稳定片段 → 理由」。
 * 这些都不是显示文案：正则、送往后端的数据、以及与后端返回文本比对的匹配串。
 */
const ALLOWED: Record<string, Array<{ contains: string; reason: string }>> = {
  'pages/ChatPage.tsx': [
    { contains: 'stockContext:', reason: '送给后端的股票名，属于数据不是文案' },
    { contains: 'COMPARE_STOCK_MESSAGE_RE', reason: '意图判断正则，属于程序逻辑' },
    { contains: 'SWITCH_STOCK_MESSAGE_RE', reason: '意图判断正则，属于程序逻辑' },
  ],
  'api/error.ts': [
    { contains: 'hasMissingParamText', reason: '与后端错误文本比对的匹配串' },
    { contains: 'screening_unavailable', reason: '与后端错误文本比对的匹配串' },
  ],
  'pages/AlertsPage.tsx': [],
  'pages/PortfolioPage.tsx': [],
  'components/alerts/AlertTriggerHistory.tsx': [],
  'utils/portfolioFormat.ts': [],
};

/** 去掉注释与三语表结构，剩下的中文才可能是写死的显示字串。 */
function sourceOf(rel: string): string {
  const key = Object.keys(SOURCES).find((k) => k.endsWith(`/${rel}`));
  if (!key) throw new Error(`找不到原始码：${rel}`);
  return SOURCES[key];
}

function scannableLines(rel: string): Array<{ line: string; index: number }> {
  const raw = sourceOf(rel)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
    .replace(/^\s*'?(?:zh|zh-tw|en|ko)'?:\s*'[^']*',?\s*$/gm, '');
  return raw
    .split('\n')
    .map((line: string, index: number) => ({ line: line.trim(), index: index + 1 }))
    .filter(({ line }: { line: string }) => CJK.test(line));
}

describe('no hardcoded Chinese in i18n-converted files', () => {
  for (const [rel, allowed] of Object.entries(ALLOWED)) {
    it(`${rel} routes every user-facing string through t()`, () => {
      const offenders = scannableLines(rel)
        .filter(({ line }) => !allowed.some((a) => line.includes(a.contains)))
        .map((o) => `${rel}:${o.index}  ${o.line.slice(0, 90)}`);

      expect(offenders).toEqual([]);
    });
  }

  it('keeps the allowlist honest — every entry must still match something', () => {
    // 白名单条目失效（对应代码已删除或改写）时应该清理，否则它会悄悄放行别的东西。
    const stale: string[] = [];
    for (const [rel, allowed] of Object.entries(ALLOWED)) {
      const lines = scannableLines(rel);
      for (const entry of allowed) {
        if (!lines.some(({ line }) => line.includes(entry.contains))) {
          stale.push(`${rel}: "${entry.contains}" 已无对应代码，请从白名单移除`);
        }
      }
    }
    expect(stale).toEqual([]);
  });

  it('records which pages are not converted yet', () => {
    // 尚未改造的页面清单。改造一个就从这里移走一个 —— 这样「还剩多少」永远是显式的，
    // 不会因为没人记得而无限期停留在半完成状态。
    const pages = Object.keys(SOURCES)
      .filter((k) => /\/pages\/[^/]+\.tsx$/.test(k))
      .map((k) => k.split('/').pop() as string)
      .filter((f) => !Object.keys(ALLOWED).includes(`pages/${f}`))
      .sort();
    expect(pages).toEqual([
      'BacktestPage.tsx',
      'DecisionSignalsPage.tsx',
      'HomePage.tsx',
      'LoginPage.tsx',
      'NotFoundPage.tsx',
      'SettingsPage.tsx',
      'StockScreeningPage.tsx',
      'TokenUsagePage.tsx',
    ]);
  });
});
