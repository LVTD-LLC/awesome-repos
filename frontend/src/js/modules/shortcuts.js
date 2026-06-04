const READY_ATTRIBUTE = "data-navigation-shortcuts-ready";
const RESERVED_LINK_SHORTCUT_KEYS = new Set(["j", "k", "n", "p"]);

export function initNavigationShortcuts(root = document) {
  if (document.documentElement.hasAttribute(READY_ATTRIBUTE)) {
    return;
  }
  document.documentElement.setAttribute(READY_ATTRIBUTE, "true");

  const shortcuts = buildShortcutList(root);

  document.addEventListener("keydown", (event) => {
    if (shouldIgnoreShortcutEvent(event)) {
      return;
    }

    handleShortcutKeydown(event, shortcuts);
  });
}

function buildShortcutList(root) {
  const shortcuts = [
    {
      key: "/",
      label: "Search repositories",
      run: () => focusSearchInput(root),
    },
  ];

  collectListShortcuts(root).forEach((shortcut) => shortcuts.push(shortcut));
  collectPaginationShortcuts(root).forEach((shortcut) => shortcuts.push(shortcut));
  collectLinkShortcuts(root).forEach((shortcut) => shortcuts.push(shortcut));

  shortcuts.push({
    key: "t",
    label: "Toggle theme",
    run: () => clickFirstVisible(root.querySelectorAll("[data-theme-toggle]")),
  });

  return shortcuts;
}

function collectListShortcuts(root) {
  return [
    {
      key: "j",
      label: "Next list item",
      when: () => shortcutListItems(root).length > 0,
      run: () => moveListSelection(root, "next"),
    },
    {
      key: "k",
      label: "Previous list item",
      when: () => shortcutListItems(root).length > 0,
      run: () => moveListSelection(root, "previous"),
    },
  ];
}

function collectPaginationShortcuts(root) {
  return [
    {
      key: "n",
      label: "Next page",
      resolve: () => paginationLink(root, "next"),
      run: (link) => navigateTo(link.href),
    },
    {
      key: "p",
      label: "Previous page",
      resolve: () => paginationLink(root, "previous"),
      run: (link) => navigateTo(link.href),
    },
  ];
}

function collectLinkShortcuts(root) {
  const seenKeys = new Set();
  const shortcuts = [];

  root.querySelectorAll("[data-shortcut-link]").forEach((link) => {
    const key = normalizeShortcutKey(link.dataset.shortcutKey);
    const label = link.dataset.shortcutLabel || link.textContent.trim();

    if (!key || !label || seenKeys.has(key) || RESERVED_LINK_SHORTCUT_KEYS.has(key)) {
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

function handleShortcutKeydown(event, shortcuts) {
  const pressedKey = normalizeEventKey(event);
  if (!pressedKey) {
    return;
  }

  const shortcut = shortcuts.find((candidate) => candidate.key === pressedKey);
  if (!shortcut) {
    return;
  }

  if (shortcut.when && !shortcut.when()) {
    return;
  }

  const resolvedTarget = shortcut.resolve ? shortcut.resolve() : null;
  if (shortcut.resolve && !resolvedTarget) {
    return;
  }

  event.preventDefault();
  shortcut.run(resolvedTarget);
}

function shouldIgnoreShortcutEvent(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.isComposing) {
    return true;
  }

  if (hasOpenDialog()) {
    return true;
  }

  return isTypingTarget(event.target);
}

function hasOpenDialog() {
  return Array.from(document.querySelectorAll("[role='dialog'][aria-modal='true']")).some(isVisible);
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

function moveListSelection(root, direction) {
  const items = shortcutListItems(root);
  if (!items.length) {
    return;
  }

  const currentIndex = currentListItemIndex(items);
  const targetIndex = nextListItemIndex(currentIndex, items.length, direction);
  const item = items[targetIndex];
  const target = listItemTarget(item);

  items.forEach((candidate) => candidate.classList.remove("shortcut-list-item-active"));
  item.classList.add("shortcut-list-item-active");

  if (target && typeof target.focus === "function") {
    target.focus({ preventScroll: true });
  }

  item.scrollIntoView({ block: "nearest" });
}

function currentListItemIndex(items) {
  const focusedIndex = items.findIndex((item) => item.contains(document.activeElement));
  if (focusedIndex >= 0) {
    return focusedIndex;
  }

  return items.findIndex((item) => item.classList.contains("shortcut-list-item-active"));
}

function nextListItemIndex(currentIndex, length, direction) {
  if (currentIndex < 0) {
    return direction === "next" ? 0 : length - 1;
  }

  const offset = direction === "next" ? 1 : -1;
  return Math.min(Math.max(currentIndex + offset, 0), length - 1);
}

function shortcutListItems(root) {
  return Array.from(root.querySelectorAll("[data-shortcut-list-item]")).filter(isVisible);
}

function listItemTarget(item) {
  return item.querySelector("[data-shortcut-list-target], a[href], button:not([disabled])");
}

function paginationLink(root, direction) {
  return firstVisible(root.querySelectorAll(`[data-shortcut-page="${direction}"]`));
}

function clickFirstVisible(elements) {
  const element = firstVisible(elements) || elements[0];
  if (element) {
    element.click();
  }
}

function firstVisible(elements) {
  return Array.from(elements).find(isVisible);
}

function isVisible(element) {
  const style = window.getComputedStyle(element);
  return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
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
