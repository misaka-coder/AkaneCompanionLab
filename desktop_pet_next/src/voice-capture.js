// getUserMedia itself cannot be aborted. Bound the wait and release any stream
// that arrives after cancellation, without letting it start a recording/call.
export function captureMicrophone({ getUserMedia, signal, timeoutMs = 15000 }) {
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer;
    const finish = (error, stream) => {
      if (settled) {
        for (const track of stream?.getTracks?.() || []) track.stop();
        return;
      }
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve(stream);
    };
    const abort = () => finish(Object.assign(new Error("麦克风打开已取消"), { name: "AbortError" }));
    if (signal?.aborted) {
      abort();
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => finish(Object.assign(
      new Error("打开麦克风超时，请检查设备与麦克风权限后重试。"),
      { name: "TimeoutError" }
    )), timeoutMs);
    Promise.resolve().then(() => {
      if (settled) return;
      return getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true
      } });
    }).then((stream) => finish(null, stream), (error) => finish(error));
  });
}
