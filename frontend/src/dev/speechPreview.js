export const createSpeechPreviewSession = ({ isDev, search }) => {
  const enabled = Boolean(isDev)
    && new URLSearchParams(search || '').get('speech-preview') === '1';
  if (!enabled) return null;

  return {
    uid: 'speech-preview',
    roomId: 'speech-preview',
    uname: '界面预览',
    face: '',
    level: 6,
    money: 0,
    bcoin: 0,
    following: 0,
    follower: 0,
    dynamic_count: 0,
    current_exp: 0,
    next_exp: 1,
  };
};
