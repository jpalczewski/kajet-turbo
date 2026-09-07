/** Copies `text` to the clipboard, returning false instead of throwing when access is
 * denied (permissions, insecure context, browser policy) -- callers decide how to
 * surface that instead of leaking an unhandled rejection. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
