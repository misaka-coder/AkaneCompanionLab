export function formatError(error) {
  return error instanceof Error ? error.message : String(error || "unknown");
}
