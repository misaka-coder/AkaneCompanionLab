// Serialize native imports without losing a second drop or moving queued files
// to another bot/session. A rejected import must not poison the queue.
export function createWorkspaceImportQueue({ readScope, run }) {
  let tail = Promise.resolve();
  return (paths) => {
    const scope = { ...readScope() };
    const key = JSON.stringify(scope);
    const changed = () => JSON.stringify(readScope()) !== key;
    const cancelled = () => ({ ok: false, status: "cancelled", reason: "attachment_scope_changed" });
    const task = tail.then(async () => {
      if (changed()) return cancelled();
      try {
        const result = await run([...paths], scope);
        return changed() ? cancelled() : result;
      } catch (error) {
        if (changed()) return cancelled();
        throw error;
      }
    });
    tail = task.catch(() => {});
    return task;
  };
}
