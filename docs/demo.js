"use strict";

document.documentElement.classList.add("js");

// The site never connects to DCS, a microphone or an AI service.
const menuToggle = document.querySelector(".menu-toggle");
const navigation = document.getElementById("main-nav");
function closeMenu() {
  menuToggle.setAttribute("aria-expanded", "false");
  menuToggle.setAttribute("aria-label", "Open navigation");
  navigation.dataset.open = "false";
}
menuToggle.addEventListener("click", () => {
  const open = menuToggle.getAttribute("aria-expanded") !== "true";
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  navigation.dataset.open = String(open);
});
navigation.addEventListener("click", (event) => {
  if (event.target.closest("a")) closeMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && menuToggle.getAttribute("aria-expanded") === "true") {
    closeMenu();
    menuToggle.focus();
  }
});

const shots = {
  overlay: {
    src: "assets/screenshots/overlay.png", label: "OVERLAY",
    alt: "Actual DCS Copilote overlay showing an example conversation in French, the Hornet aircraft and microphone controls",
    caption: "A compact, movable window for questions and streaming answers.",
  },
  checklist: {
    src: "assets/screenshots/checklist.png", label: "CHECKLIST",
    alt: "Actual checklist UI with sample briefing steps, the current step highlighted, and previous, repeat and next buttons",
    caption: "Your current step stays in view. Repeat, go back, or move on with a brief PTT press.",
  },
  ai: {
    src: "assets/screenshots/settings-ai.png", label: "AI & MODELS",
    alt: "Actual French settings screen with AI providers, endpoint, API key and model configuration; no credentials are present",
    caption: "Choose your provider and models, including fallbacks when a service is unavailable.",
  },
  voice: {
    src: "assets/screenshots/settings-voice.png", label: "VOICE SETTINGS",
    alt: "Actual French voice settings showing speech recognition, voice engine and speech configuration",
    caption: "Pick speech recognition and synthesis settings to fit your setup.",
  },
};
const tabs = Array.from(document.querySelectorAll("[data-shot]"));
const galleryImage = document.getElementById("gallery-image");
const galleryZoom = document.getElementById("gallery-zoom");
const screenshotPanel = document.getElementById("screenshot-panel");

function selectShot(tab) {
  const shot = shots[tab.dataset.shot];
  if (!shot) return;
  for (const item of tabs) {
    const active = item === tab;
    item.setAttribute("aria-selected", String(active));
    item.tabIndex = active ? 0 : -1;
  }
  galleryImage.src = shot.src;
  galleryImage.alt = shot.alt;
  document.getElementById("shot-label").textContent = shot.label;
  document.getElementById("gallery-caption").textContent = shot.caption;
  screenshotPanel.setAttribute("aria-labelledby", tab.id);
  galleryZoom.dataset.zoom = shot.src;
  galleryZoom.dataset.caption = `${shot.caption} Actual French UI with sample data.`;
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener("click", () => selectShot(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    selectShot(tabs[next]);
    tabs[next].focus();
  });
}

const dialog = document.getElementById("screenshot-dialog");
const dialogImage = document.getElementById("dialog-image");
for (const button of document.querySelectorAll("[data-zoom]")) {
  button.addEventListener("click", () => {
    dialogImage.src = button.dataset.zoom;
    dialogImage.alt = button.querySelector("img").alt;
    document.getElementById("dialog-caption").textContent = button.dataset.caption;
    dialog.showModal();
  });
}
document.getElementById("close-dialog").addEventListener("click", () => dialog.close());
dialog.addEventListener("click", (event) => {
  const rect = dialog.getBoundingClientRect();
  if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right ||
      event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
});

const copyButton = document.getElementById("copy-install");
copyButton.addEventListener("click", async () => {
  const status = document.getElementById("copy-status");
  const commands = document.getElementById("install-command");
  try {
    await navigator.clipboard.writeText(commands.textContent);
    copyButton.textContent = "Copied ✓";
    status.textContent = "Installation commands copied to clipboard.";
    setTimeout(() => { copyButton.textContent = "Copy ⧉"; }, 2500);
  } catch {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(commands);
    selection.removeAllRanges();
    selection.addRange(range);
    status.textContent = "Commands selected. Press Control+C or Command+C to copy.";
  }
});
