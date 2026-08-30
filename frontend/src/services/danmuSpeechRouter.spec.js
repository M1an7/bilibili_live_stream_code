import { describe, expect, it, vi } from 'vitest';
import { routeDanmuToSpeech } from './danmuSpeechRouter';

describe('routeDanmuToSpeech', () => {
  it('routes trimmed ordinary danmu without the username', () => {
    const service = { enqueue: vi.fn(() => true) };

    expect(routeDanmuToSpeech(
      { type: 'danmu', uname: '观众甲', msg: '  你好主播  ' },
      service,
      () => 123,
    )).toBe(true);
    expect(service.enqueue).toHaveBeenCalledWith('你好主播', { createdAt: 123 });
  });

  it.each(['gift', 'interact', 'system'])(
    'does not route %s events in milestone zero',
    (type) => {
      const service = { enqueue: vi.fn() };

      expect(routeDanmuToSpeech({ type, msg: '忽略' }, service)).toBe(false);
      expect(service.enqueue).not.toHaveBeenCalled();
    },
  );

  it('rejects missing and blank messages', () => {
    const service = { enqueue: vi.fn() };

    expect(routeDanmuToSpeech({ type: 'danmu', msg: '   ' }, service)).toBe(false);
    expect(routeDanmuToSpeech(null, service)).toBe(false);
    expect(service.enqueue).not.toHaveBeenCalled();
  });

  it('reports rejection when speech is disabled', () => {
    const service = { enqueue: vi.fn(() => false) };

    expect(routeDanmuToSpeech({ type: 'danmu', msg: '不会播放' }, service)).toBe(false);
  });
});
