import { describe, expect, it } from 'vitest';
import { createSpeechPreviewSession } from './speechPreview';

describe('createSpeechPreviewSession', () => {
  it('creates a fake local user only for the explicit development preview URL', () => {
    expect(createSpeechPreviewSession({ isDev: true, search: '?speech-preview=1' })).toMatchObject({
      uid: 'speech-preview',
      roomId: 'speech-preview',
      uname: '界面预览',
    });
  });

  it('never creates a fake session in production', () => {
    expect(createSpeechPreviewSession({ isDev: false, search: '?speech-preview=1' })).toBeNull();
  });

  it('does not change a normal development page', () => {
    expect(createSpeechPreviewSession({ isDev: true, search: '' })).toBeNull();
  });
});
