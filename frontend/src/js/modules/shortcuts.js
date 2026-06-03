const SHORTCUT_TIMEOUT_MS = 900;
const READY_ATTRIBUTE = "data-navigation-shortcuts-ready";

export function initNavigationShortcuts(root = document) {
  if (document.documentElement.hasAttribute(READY_ATTRIBUTE)) {
    return;
  }
  document.documentElement.setAttribute(READY_ATTRIBUTE, "true");

  const state = {
    helpDialog: null,
    lastActiveElement: null,
    prefix: "",
    prefixTimeoutId: null,
  };

  const shortcuts = buildShortcutList(root, state);

  root.querySelectorAll("[data-shortcuts-open]").forEach((button) => {
    button.addEventListener("click", () => openHelpDialog(shortcuts, state));
  });

  document.addEventListener("keydown", (event) => {
    if (isHelpDialogOpen(state)) {
      handleHelpDialogKeydown(event, state);
      return;
    }

    if (shouldIgnoreShortcutEvent(event)) {
      clearPrefix(state);
      return;
    }

    handleShortcutKeydown(event, shortcuts, state);
  });
}

function buildShortcutList(root, state) {
  const shortcuts = [
    {
      key: "/",
      label: "Search repositories",
      run: () => focusSearchInput(root),
    },
    {
      key: "?",
      label: "Show shortcuts",
      run: () => openHelpDialog(shortcuts, state),
    },
  ];

  collectLinkShortcuts(root).forEach((shortcut) => shortcuts.push(shortcut));

  shortcuts.push(
    {
      key: "t",
      label: "Toggle theme",
      run: () => clickFirstVisible(root.querySelectorAll("[data-theme-toggle]")),
    },
    {
      key: "escape",
      label: "Close shortcuts",
      run: () => closeHelpDialog(state),
      helpOnly: true,
    },
  );

  return shortcuts;
}

function collectLinkShortcuts(root) {
  const seenKeys = new Set();
  const shortcuts = [];

  root.querySelectorAll("[data-shortcut-link]").forEach((link) => {
    const key = normalizeShortcutKey(link.dataset.shortcutKey);
    const label = link.dataset.shortcutLabel || link.textContent.trim();

    if (!key || !label || seenKeys.has(key)) {
      return;
    }

    seenKeys.add(key);
    shortcuts.push({
      key,
      label,
      run: () => navigateTo(link.href),
    });
  });

  return shortcuts;
}

function handleShortcutKeydown(event, shortcuts, state) {
  const pressedKey = normalizeEventKey(event);
  if (!pressedKey) {
    clearPrefix(state);
    return;
  }

  if (state.prefix) {
    const shortcut = shortcuts.find((candidate) => candidate.key === `${state.prefix} ${pressedKey}`);

    if (shortcut) {
      event.preventDefault();
      clearPrefix(state);
      shortcut.run();
      return;
    }

    clearPrefix(state);
    return;
  }

  const hasSequence = shortcuts.some((shortcut) => shortcut.key.startsWith(`${pressedKey} `));
  if (hasSequence) {
    event.preventDefault();
    state.prefix = pressedKey;
    window.clearTimeout(state.prefixTimeoutId);
    state.prefixTimeoutId = window.setTimeout(() => clearPrefix(state), SHORTCUT_TIMEOUT_MS);
    return;
  }

  const shortcut = shortcuts.find((candidate) => !candidate.helpOnly && candidate.key === pressedKey);
  if (!shortcut) {
    clearPrefix(state);
    return;
  }

  event.preventDefault();
  shortcut.run();
}

function shouldIgnoreShortcutEvent(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.isComposing) {
    return true;
  }

  return isTypingTarget(event.target);
}

function isTypingTarget(target) {
  if (!(target instanceof Element)) {
    return false;
  }

  if (target.closest("input, textarea, select, [contenteditable='true'], [contenteditable=''], [role='textbox']")) {
    return true;
  }

  return target.isContentEditable;
}

function normalizeEventKey(event) {
  if (event.key === "Escape") {
    return "escape";
  }

  if (event.key.length === 1) {
    return event.key.toLowerCase();
  }

  return "";
}

function normalizeShortcutKey(key) {
  return String(key || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, " ");
}

function clearPrefix(state) {
  state.prefix = "";
  window.clearTimeout(state.prefixTimeoutId);
  state.prefixTimeoutId = null;
}

function focusSearchInput(root) {
  const searchInput = firstVisible(root.querySelectorAll("[data-shortcut-search]"));
  if (!searchInput) {
    navigateTo(searchFormAction(root));
    return;
  }

  searchInput.focus({ preventScroll: true });
  if (typeof searchInput.select === "function") {
    searchInput.select();
  }
}

function clickFirstVisible(elements) {
  const element = firstVisible(elements) || elements[0];
  if (element) {
    element.click();
  }
}

function firstVisible(elements) {
  return Array.from(elements).find((element) => {
    const style = window.getComputedStyle(element);
    return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
  });
}

function navigateTo(href) {
  if (!href) {
    return;
  }

  window.location.assign(href);
}

function searchFormAction(root) {
  const searchControl = root.querySelector("[data-shortcut-search]");
  const form = searchControl ? searchControl.closest("form") : null;
  return form ? form.action : "";
}

function openHelpDialog(shortcuts, state) {
  if (!state.helpDialog) {
    state.helpDialog = buildHelpDialog(shortcuts, state);
    document.body.appendChild(state.helpDialog);
  }

  state.lastActiveElement = document.activeElement;
  state.helpDialog.classList.remove("hidden");
  document.body.classList.add("overflow-hidden");

  const closeButton = state.helpDialog.querySelector("button[data-shortcuts-close]");
  if (closeButton) {
    closeButton.focus({ preventScroll: true });
  }
}

function closeHelpDialog(state) {
  if (!state.helpDialog) {
    return;
  }

  state.helpDialog.classList.add("hidden");
  document.body.classList.remove("overflow-hidden");

  if (state.lastActiveElement && typeof state.lastActiveElement.focus === "function") {
    state.lastActiveElement.focus({ preventScroll: true });
  }
}

function isHelpDialogOpen(state) {
  return state.helpDialog && !state.helpDialog.classList.contains("hidden");
}

function buildHelpDialog(shortcuts, state) {
  const dialog = document.createElement("div");
  dialog.dataset.shortcutsDialog = "";
  dialog.className = "fixed inset-0 z-50 hidden";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-labelledby", "keyboard-shortcuts-title");

  const backdrop = document.createElement("div");
  backdrop.className = "absolute inset-0 h-full w-full cursor-default bg-gray-950/45 backdrop-blur-sm";
  backdrop.dataset.shortcutsBackdrop = "";
  backdrop.setAttribute("aria-hidden", "true");
  backdrop.addEventListener("click", () => closeHelpDialog(state));

  const panel = document.createElement("div");
  panel.className = "absolute left-1/2 top-20 max-h-[calc(100vh-8rem)] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl shadow-gray-950/20 dark:border-gray-800 dark:bg-gray-950 dark:shadow-black/50 sm:top-24";

  const header = document.createElement("div");
  header.className = "flex items-center justify-between gap-4 border-b border-gray-200 px-5 py-4 dark:border-gray-800";

  const title = document.createElement("h2");
  title.id = "keyboard-shortcuts-title";
  title.className = "text-sm font-black text-gray-950 dark:text-white";
  title.textContent = "Keyboard shortcuts";

  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.dataset.shortcutsClose = "";
  closeButton.className = "inline-flex h-9 w-9 items-center justify-center rounded-full border border-gray-200 bg-white text-gray-700 transition hover:bg-gray-50 hover:text-gray-950 focus:outline-none focus-visible:ring-2 focus-visible:ring-green-500 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:border-gray-800 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white dark:focus-visible:ring-offset-gray-950";
  closeButton.setAttribute("aria-label", "Close keyboard shortcuts");
  closeButton.innerHTML = '<svg class="h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>';
  closeButton.addEventListener("click", () => closeHelpDialog(state));

  header.append(title, closeButton);

  const list = document.createElement("div");
  list.className = "max-h-[calc(100vh-13rem)] overflow-y-auto divide-y divide-gray-200 dark:divide-gray-800";

  shortcuts.forEach((shortcut) => {
    list.appendChild(buildShortcutRow(shortcut));
  });

  panel.append(header, list);
  dialog.append(backdrop, panel);

  return dialog;
}

function buildShortcutRow(shortcut) {
  const row = document.createElement("div");
  row.className = "flex items-center justify-between gap-4 px-5 py-3";

  const label = document.createElement("span");
  label.className = "min-w-0 text-sm font-semibold text-gray-700 dark:text-gray-200";
  label.textContent = shortcut.label;

  const keyGroup = document.createElement("span");
  keyGroup.className = "flex shrink-0 items-center gap-1.5";

  shortcut.key.split(" ").forEach((key) => {
    const keyElement = document.createElement("kbd");
    keyElement.className = "inline-flex min-w-8 items-center justify-center rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 font-mono text-xs font-bold text-gray-700 shadow-sm dark:border-gray-700 dark:bg-gray-900 dark:text-gray-200";
    keyElement.textContent = formatKey(key);
    keyGroup.appendChild(keyElement);
  });

  row.append(label, keyGroup);
  return row;
}

function formatKey(key) {
  if (key === "escape") {
    return "Esc";
  }

  if (key.length === 1 && /[a-z]/.test(key)) {
    return key.toUpperCase();
  }

  return key;
}

function handleHelpDialogKeydown(event, state) {
  if (event.key === "Escape") {
    event.preventDefault();
    closeHelpDialog(state);
    return;
  }

  if (event.key === "Tab") {
    trapDialogFocus(event, state.helpDialog);
  }
}

function trapDialogFocus(event, dialog) {
  const focusableElements = Array.from(
    dialog.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex='-1'])"),
  ).filter((element) => window.getComputedStyle(element).display !== "none");

  if (!focusableElements.length) {
    return;
  }

  const first = focusableElements[0];
  const last = focusableElements[focusableElements.length - 1];

  if (!dialog.contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
    return;
  }

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
    return;
  }

  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}
