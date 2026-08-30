export const routeDanmuToSpeech = (message, service, now = Date.now) => {
  if (!message || message.type !== 'danmu') return false;
  const text = typeof message.msg === 'string' ? message.msg.trim() : '';
  if (!text || !service || typeof service.enqueue !== 'function') return false;
  return service.enqueue(text, { createdAt: now() }) !== false;
};
