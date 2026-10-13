
export function buildReadinessRow({ ok, label, detail }) {
  const row = document.createElement("div");
  row.className = `readiness-row ${ok ? "ok" : "todo"}`;

  const mark = document.createElement("span");
  mark.className = "readiness-mark";
  mark.setAttribute("aria-hidden", "true");
  mark.textContent = ok ? "✓" : "!";

  const body = document.createElement("div");
  const title = document.createElement("strong");
  const desc = document.createElement("span");
  title.textContent = label;
  desc.textContent = detail;
  body.append(title, desc);
  row.append(mark, body);
  return row;
}

export function buildTechnicalDetails(rows) {
  const details = document.createElement("details");
  details.className = "technical-details";
  const summary = document.createElement("summary");
  summary.textContent = "技术信息";
  const list = document.createElement("div");
  list.className = "technical-details-list";
  list.append(
    ...rows.map(([label, value]) => {
      const row = document.createElement("p");
      row.append(buildText("span", label), buildText("code", value || "-"));
      return row;
    })
  );
  details.append(summary, list);
  return details;
}

export function buildText(tagName, text) {
  const element = document.createElement(tagName);
  element.textContent = text;
  return element;
}

export function setFieldValue(el, value) {
  if (el) el.value = String(value ?? "");
}

export function flashElement(element, className) {
  if (!element || !className) return;
  element.classList.remove(className);
  void element.offsetWidth;
  element.classList.add(className);
}

export function buildImageLoadError(text, className = "image-load-error") {
  const fallback = document.createElement("div");
  fallback.className = className;
  fallback.textContent = text;
  return fallback;
}
