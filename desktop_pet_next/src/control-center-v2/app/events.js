export function initialPageFromLocation(search = window.location.search) {
  const page = new URLSearchParams(search).get("page") || "overview";
  return { character: "appearance", advanced: "system", diagnostics: "system", model: "model" }[page]
    || (["overview", "chat", "appearance", "voice", "abilities", "system", "model"].includes(page) ? page : "overview");
}

export function actionValueFromButton(button) {
  if (!Object.hasOwn(button.dataset, "actionValue")) return undefined;
  const value = String(button.dataset.actionValue || "").trim();
  if (button.dataset.actionValueType === "boolean") return value === "true";
  if (button.dataset.actionValueType === "number") return Number(value);
  return value;
}

export function bindControlCenterEvents({ root, store, actions, model, drafts, voice, capabilities, chat,
  presentation, renderer, snapshot, windowRef = window }) {
  const removals = [];
  let focusTimer;
  const on = (target, event, callback, capture = false) => {
    target.addEventListener(event, callback, capture);
    removals.push(() => target.removeEventListener(event, callback, capture));
  };
  on(root, "click", event => {
    const computer = event.target.closest("button[data-computer-use]");
    if (computer) {
      if (!computer.disabled) void actions.runAction(`computerUse.${computer.dataset.computerUse}`, {
        targetId: root.querySelector("[data-computer-use-target]")?.value || "" });
      return;
    }
    if ([capabilities, chat, voice, model, presentation].some(controller => controller.handleClick(event))) return;
    const page = event.target.closest("[data-page]");
    if (page) { store.patch({ activePage: page.dataset.page || "overview" }); return; }
    if (event.target.closest("[data-refresh]")) { void snapshot.refresh(); return; }
    const button = event.target.closest("[data-action]");
    if (!button || button.disabled || (button.matches('button[type="submit"]') && button.closest("form"))) return;
    const actionId = button.dataset.action;
    const value = actionValueFromButton(button);
    void actions.runAction(actionId, actionId === "voice.previewPlay" ? voice.previewPayload() : value === undefined ? {} : { value });
  });
  on(root, "input", event => {
    if (event.target.matches("[data-memory-group-filter]")) store.patch({ memoryGroupFilter: event.target.value });
    for (const controller of [chat, voice, model, presentation]) controller.handleInput(event); drafts.handleEdit(event);
  });
  on(root, "change", event => {
    const choice = event.target.closest("[data-perception-choice]");
    if (choice && !choice.disabled) {
      if (!choice.reportValidity()) return;
      const value = choice.dataset.valueType === "number" ? Number(choice.value) : choice.value;
      choice.blur(); void actions.runAction(choice.dataset.perceptionChoice, { value }); return;
    }
    const bot = event.target.closest("[data-bound-bot-select]");
    if (bot && !bot.disabled) void actions.runAction("settings.selectBot", { value: bot.value, botId: bot.value });
    for (const controller of [model, voice, presentation]) controller.handleChange(event);
    drafts.handleEdit(event);
  });
  on(root, "keydown", event => { if (!presentation.handleKeydown(event)) chat.handleKeydown(event); });
  on(root, "submit", event => { if (!capabilities.handleSubmit(event)) chat.handleSubmit(event); });
  on(root, "focusout", () => {
    windowRef.clearTimeout(focusTimer);
    focusTimer = windowRef.setTimeout(renderer.flush, 60);
  });
  for (const [name, method, capture] of [
    ["paste", "handlePaste"], ["dragover", "handleDragover"], ["drop", "handleDrop"],
    ["scroll", "handleScroll", true], ["error", "handleError", true]
  ]) on(root, name, chat[method], capture);
  for (const name of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {
    on(root, name, event => {
      const wasDragging = presentation.isDragging();
      presentation["handle" + name[0].toUpperCase() + name.slice(1)](event);
      if (wasDragging && !presentation.isDragging()) renderer.flush();
    });
  }
  on(windowRef, "resize", chat.refreshPreviews);
  return () => { windowRef.clearTimeout(focusTimer); for (const remove of removals) remove(); };
}
