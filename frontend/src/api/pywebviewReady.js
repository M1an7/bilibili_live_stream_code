export const createPywebviewWaiter = ({
  target,
  isDev,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) => {
  return () => {
    if (target.pywebview) {
      return Promise.resolve(true);
    }

    return new Promise((resolve) => {
      let timer = null;

      const cleanup = () => {
        target.removeEventListener('pywebviewready', onReady);
        if (timer !== null) {
          clearTimer(timer);
        }
      };

      const finish = () => {
        cleanup();
        resolve(Boolean(target.pywebview));
      };

      const onReady = () => finish();

      target.addEventListener('pywebviewready', onReady);

      // 浏览器开发预览没有 Python 桥接，需要保留有限等待后的 mock 回退。
      // 桌面生产版必须持续等待，避免单文件 EXE 首次解压较慢时误判失败。
      if (isDev) {
        timer = setTimer(finish, 3_000);
      }
    });
  };
};
