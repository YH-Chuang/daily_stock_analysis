import { describe, expect, it } from 'vitest';

import { UI_TEXT, type UiTextKey } from '../uiText';

const zh = UI_TEXT.zh;
const zhTw = UI_TEXT['zh-tw'];
const en = UI_TEXT.en;

describe('zh-tw UI text table', () => {
  it('covers exactly the same keys as zh and en', () => {
    expect(Object.keys(zhTw).sort()).toEqual(Object.keys(zh).sort());
    expect(Object.keys(zhTw).sort()).toEqual(Object.keys(en).sort());
  });

  it('preserves every placeholder from the zh source', () => {
    const placeholders = (value: string) => (value.match(/\{[a-zA-Z_]\w*\}/g) ?? []).sort();
    const mismatched = (Object.keys(zh) as UiTextKey[]).filter(
      (key) => placeholders(zh[key]).join(',') !== placeholders(zhTw[key]).join(',')
    );
    expect(mismatched).toEqual([]);
  });

  it('never ships Mainland vocabulary in the Traditional table', () => {
    // 该改而没改：字形转了、用词没换。
    const MAINLAND = [
      '數據', '信息', '網絡', '軟件', '硬件', '服務器', '用戶', '默認', '緩存',
      '視頻', '質量', '倉位', '板塊', '止損', '止盈', '賬號', '實時', '後臺', '屏幕',
    ];
    const offenders: string[] = [];
    for (const key of Object.keys(zhTw) as UiTextKey[]) {
      for (const word of MAINLAND) {
        if (zhTw[key].includes(word)) {
          offenders.push(`${key}: ${zhTw[key]}`);
          break;
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('never ships the known over-conversions of automated s2twp', () => {
    // 改错了：OpenCC s2twp 会把这些词转过头，逐条钉死避免重跑转换后复发。
    //   复盘 是「检讨盘势」不是「覆盖」-> 復盤
    //   Docker 镜像 -> 映像；重载 -> 重新载入（不是 overload）
    //   股票代码 -> 代碼（不是原始码的 程式碼）
    const OVER_CONVERTED: Array<[string, string]> = [
      ['覆盤', '復盤'],
      ['全域性', '全域'],
      ['映象', '映像'],
      ['控制檯', '主控台'],
      ['工作臺', '工作台'],
      ['過載', '重新載入'],
      ['釋出', '發布'],
      ['併發', '並行'],
      ['股票程式碼', '股票代碼'],
      ['錯誤程式碼', '錯誤代碼'],
      ['貴州茅臺', '貴州茅台'],
    ];
    const offenders: string[] = [];
    for (const key of Object.keys(zhTw) as UiTextKey[]) {
      for (const [wrong, right] of OVER_CONVERTED) {
        if (zhTw[key].includes(wrong)) {
          offenders.push(`${key}: 含「${wrong}」，应为「${right}」-> ${zhTw[key]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it('keeps the zh and en tables untouched', () => {
    // 纯加法：新增繁体不得改动既有两种语言。
    expect(zh['home.marketReview']).toBe('大盘复盘');
    expect(en['home.marketReview']).toBe('Market review');
    expect(zhTw['home.marketReview']).toBe('大盤復盤');
  });
});
