/** Parenthetical narration is presentation only; preserve every source character. */
export function splitReplyText(value) {
  const source = String(value ?? "");
  const runs = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < source.length; index++) {
    const char = source[index];
    if (char === "（" || char === "(") {
      if (depth === 0) {
        if (index > start) runs.push({ text: source.slice(start, index), aside: false });
        start = index;
      }
      depth++;
    } else if ((char === "）" || char === ")") && depth > 0) {
      depth--;
      if (depth === 0) {
        runs.push({ text: source.slice(start, index + 1), aside: true });
        start = index + 1;
      }
    }
  }
  // Streaming text may not have reached the closing parenthesis yet.
  if (start < source.length) runs.push({ text: source.slice(start), aside: depth > 0 });
  return runs;
}

export function setReplyText(element, value, { highlight = true } = {}) {
  const source = String(value ?? "");
  const document = element.ownerDocument;
  const runs = highlight ? splitReplyText(source) : [];
  if (!document || !element.replaceChildren || !runs.some(run => run.aside)) {
    element.textContent = source;
    return;
  }
  element.replaceChildren(...runs.map(run => {
    if (!run.aside) return document.createTextNode(run.text);
    const span = document.createElement("span");
    span.className = "reply-aside";
    span.textContent = run.text;
    return span;
  }));
}
