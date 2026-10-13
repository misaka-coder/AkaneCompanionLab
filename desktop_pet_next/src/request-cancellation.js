/** Cancel one waiter without cancelling a shared credential/context operation. */
export function waitWithSignal(promise, signal) {
  const pending = Promise.resolve(promise);
  if (!signal) return pending;
  const reason = () => signal.reason || new DOMException("Aborted", "AbortError");
  if (signal.aborted) { pending.catch(() => {}); return Promise.reject(reason()); }
  return new Promise((resolve, reject) => {
    const abort = () => { signal.removeEventListener("abort", abort); reject(reason()); };
    signal.addEventListener("abort", abort, { once: true });
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}
