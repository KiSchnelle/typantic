// Browser chores the log toolbar needs, done so they work where the dashboard is
// actually opened: over an SSH tunnel, or over plain http by host name.

// Save `text` as a file named `name`. The object URL is revoked a moment later,
// not right after click(): the browser may not have begun the download yet.
export function downloadText(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Copy `text` to the clipboard; whether that worked. navigator.clipboard exists
// only in a secure context, which a dashboard opened over plain http by host
// name is not; the legacy copy command still works there.
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Missing (not a secure context) or refused: fall back below.
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}
