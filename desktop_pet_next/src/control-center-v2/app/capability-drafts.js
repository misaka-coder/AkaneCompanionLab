import { serviceScope } from "./scope.js";

export function capabilityScope(viewModel) {
  return JSON.stringify([serviceScope(viewModel), viewModel?.character?.packId]);
}

export function createCapabilityDrafts({ root, store }) {
  const panels = new Map(), drafts = new Map(), revisions = new Map(), discarded = new Set();
  let owner = capabilityScope(store.getState().viewModel), renderedOwner = owner;
  let epoch = 0, pageEpoch = 0, page = store.getState().activePage;
  const keyOf = form => form?.closest("[data-capability-key]")?.dataset.capabilityKey || form?.dataset.capabilityForm || "";

  function synchronizeScope(viewModel) {
    const next = capabilityScope(viewModel);
    if (owner === next) return false;
    owner = next; epoch += 1;
    panels.clear(); drafts.clear(); revisions.clear(); discarded.clear();
    return true;
  }
  function synchronizePage(next) {
    if (next === page) return;
    page = next; pageEpoch += 1;
  }
  function changed(form) {
    const key = keyOf(form);
    revisions.set(key, (revisions.get(key) || 0) + 1);
  }
  function ticket(form) {
    const key = keyOf(form), version = revisions.get(key) || 0;
    const requestedEpoch = epoch, requestedPage = pageEpoch;
    const requestedOwner = capabilityScope(store.getState().viewModel);
    return { key, isCurrent: () => epoch === requestedEpoch && pageEpoch === requestedPage
      && requestedOwner === capabilityScope(store.getState().viewModel) && (revisions.get(key) || 0) === version };
  }
  function discard(key) {
    drafts.delete(key); discarded.add(key);
    revisions.set(key, (revisions.get(key) || 0) + 1);
  }
  function capture() {
    if (renderedOwner !== owner) return;
    for (const details of root.querySelectorAll("details[data-capability-key]")) {
      const key = details.dataset.capabilityKey;
      if (!key) continue;
      if (!discarded.has(key)) panels.set(key, details.open);
      const form = details.querySelector("[data-capability-form]");
      if (!form || form.closest("[data-capability-key]") !== details) continue;
      if (discarded.has(key)) continue;
      drafts.set(key, Array.from(form.elements).filter(element => element.name)
        .map(element => ({ name: element.name, value: element.value, checked: Boolean(element.checked), type: element.type })));
    }
  }
  function restore() {
    renderedOwner = owner;
    for (const details of root.querySelectorAll("details[data-capability-key]")) {
      const key = details.dataset.capabilityKey;
      if (!key) continue;
      if (panels.has(key)) details.open = panels.get(key);
      const draft = drafts.get(key);
      const form = details.querySelector("[data-capability-form]");
      if (!draft || !form || form.closest("[data-capability-key]") !== details) continue;
      for (const field of draft) {
        const element = form.elements.namedItem(field.name);
        if (!element) continue;
        if (field.type === "checkbox") element.checked = field.checked;
        else element.value = field.value;
      }
    }
    discarded.clear();
  }
  function handleEdit(event) {
    const form = event.target.closest?.("[data-capability-form]");
    if (form) changed(form);
  }
  return { ticket, discard, capture, restore, handleEdit, synchronizeScope, synchronizePage,
    open: key => panels.set(key, true) };
}
