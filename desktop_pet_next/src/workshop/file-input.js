
export function chooseLocalFiles({ accept = "", multiple = false } = {}) {
  return new Promise((resolve) => {
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = accept;
    fileInput.multiple = multiple;
    fileInput.hidden = true;
    document.body.appendChild(fileInput);

    let settled = false;
    const finish = (files = []) => {
      if (settled) return;
      settled = true;
      fileInput.remove();
      resolve(Array.from(files || []));
    };

    fileInput.addEventListener("change", () => finish(fileInput.files), { once: true });
    fileInput.addEventListener("cancel", () => finish(), { once: true });
  fileInput.click();
});
}

export function yieldToUiForLargeFileRead() {
  return new Promise((resolve) => {
    window.setTimeout(resolve, 0);
  });
}
