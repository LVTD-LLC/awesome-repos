const SHORTCUT_TIMEOUT_MS = 900;
const READY_ATTRIBUTE = "data-navigation-shortcuts-ready";

export function initNavigationShortcuts(root = document) {
  if (document.documentElement.hasAttribute(READY_ATTRIBUTE)) {
    return;
  }
  document.documentElement.setAttribute(READY_ATTRIBUTE, "true");

  const state = {
    prefix: "",
    prefixTimeoutId: null,
  };

  const shortcuts = buildShortcutList(root);

  document.addEventListener("keydown", (event) => {
    if (shouldIgnoreShortcutEvent(event)) {
      clearPrefix(state);
      return;
    }

    handleShortcutKeydown(event, shortcuts, state);
  });
}

function buildShortcutList(root) {
  const shortcuts = [
    {
      key: "/",
      label: "Search repositories",
      run: () => focusSearchInput(root),
    },
    {
      key: "k",
      label: "Search repositories",
      run: () => focusSearchInput(root),
    },
  ];

  collectLinkShortcuts(root).forEach((shortcut) => shortcuts.push(shortcut));

  shortcuts.push({
    key: "t",
    label: "Toggle theme",
    run: () => clickFirstVisible(root.querySelectorAll("[data-theme-toggle]")),
  });

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

  const shortcut = shortcuts.find((candidate) => candidate.key === pressedKey);
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
