import { initCopyButtons } from "./modules/copy.js";
import { initDocsEnhancements } from "./modules/docs.js";
import { initMessages, showMessage } from "./modules/messages.js";
import { initTheme } from "./modules/theme.js";
import { initUserSettingsCache } from "./modules/user-settings.js";

window.appMessages = { show: showMessage };

window.submitFeedback = async function submitFeedback(feedback) {
  const value = feedback.trim();
  if (!value) {
    return false;
  }

  const response = await fetch("/api/submit-feedback", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCookie("csrftoken"),
    },
    body: JSON.stringify({ feedback: value, page: window.location.pathname }),
  });

  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok || data.status === false) {
    throw new Error(data.message || "Failed to submit feedback. Please try again later.");
  }

  showMessage(data.message || "Feedback submitted successfully", "success");
  return true;
};

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initMessages();
  initCopyButtons();
  initDocsEnhancements();
  initUserSettingsCache();
});

function getCookie(name) {
  const cookies = document.cookie ? document.cookie.split(";") : [];

  for (const cookie of cookies) {
    const [key, ...valueParts] = cookie.trim().split("=");
    if (key === name) {
      return decodeURIComponent(valueParts.join("="));
    }
  }

  return "";
}
