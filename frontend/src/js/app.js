import { initCopyButtons } from "./modules/copy.js";
import { initDocsEnhancements } from "./modules/docs.js";
import { initMessages } from "./modules/messages.js";
import { initNavigationShortcuts } from "./modules/shortcuts.js";
import { initTheme } from "./modules/theme.js";
import { initUserSettingsCache } from "./modules/user-settings.js";

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initNavigationShortcuts();
  initMessages();
  initCopyButtons();
  initDocsEnhancements();
  initUserSettingsCache();
});
