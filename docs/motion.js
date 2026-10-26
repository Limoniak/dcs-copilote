"use strict";

// Progressive enhancement only: the full page is usable without these effects.
(() => {
  const preference = matchMedia("(prefers-reduced-motion: reduce)");
  const pointer = matchMedia("(hover: hover) and (pointer: fine)");
  const header = document.querySelector(".site-header");
  const hero = document.querySelector(".hero");
  const motionToggle = document.getElementById("motion-toggle");
  const progress = document.querySelector(".reading-progress");
  const chapterLinks = Array.from(document.querySelectorAll(".chapter-nav a"));
  const navLinks = Array.from(header.querySelectorAll('a[href^="#"]'));
  const sections = ["top", "experience", "features", "screenshots", "download", "faq"]
    .map(id => document.getElementById(id));
  let sectionTops = [];
  let frame = 0;
  let targetX = 0;
  let targetY = 0;
  let currentX = 0;
  let currentY = 0;
  let userMotion = true;
  const motionAllowed = () => !preference.matches && userMotion;

  function measure() {
    sectionTops = sections.map(section => ({
      id: section.id,
      top: section.id === "top" ? 0 : section.getBoundingClientRect().top + scrollY,
    }));
    schedule();
  }

  function render() {
    frame = 0;
    if (document.hidden) return;
    const y = Math.max(0, scrollY);
    const total = document.documentElement.scrollHeight - innerHeight;
    progress.style.setProperty("--page-progress", total > 0 ? Math.min(1, y / total) : 0);
    header.classList.toggle("is-scrolled", y > 32);
    const active = sectionTops.filter(section => section.top <= y + 160).at(-1)?.id || "top";
    for (const link of [...chapterLinks, ...navLinks]) {
      if (link.hash === `#${active}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    }

    if (motionAllowed() && innerWidth > 760 && y < hero.offsetHeight + 150) {
      currentX += (targetX - currentX) * .075;
      currentY += (targetY - currentY) * .075;
      hero.style.setProperty("--sky-x", `${currentX * -9}px`);
      hero.style.setProperty("--sky-y", `${y * .13 + currentY * -7}px`);
      hero.style.setProperty("--plane-x", `${currentX * 26 + y * .075}px`);
      hero.style.setProperty("--plane-y", `${currentY * 20 - y * .10}px`);
      hero.style.setProperty("--plane-roll", `${-3 + currentX * 1.4 - y * .002}deg`);
      hero.style.setProperty("--copy-y", `${y * -.045}px`);
      hero.style.setProperty("--product-y", `${y * -.07}px`);
      hero.style.setProperty("--wordmark-y", `${y * .055}px`);
      hero.style.setProperty("--reticle-x", `${currentX * -14}px`);
      hero.style.setProperty("--reticle-y", `${currentY * -10}px`);
      if (Math.abs(targetX - currentX) + Math.abs(targetY - currentY) > .005) schedule();
    }
  }

  function schedule() {
    if (!frame && !document.hidden) frame = requestAnimationFrame(render);
  }

  hero.addEventListener("pointermove", event => {
    if (!pointer.matches || !motionAllowed()) return;
    targetX = (event.clientX / innerWidth - .5) * 2;
    targetY = Math.max(-1, Math.min(1, ((event.clientY + scrollY) / hero.offsetHeight - .5) * 2));
    schedule();
  }, { passive: true });
  hero.addEventListener("pointerleave", () => { targetX = 0; targetY = 0; schedule(); });
  addEventListener("scroll", schedule, { passive: true });
  addEventListener("resize", measure, { passive: true });
  addEventListener("load", measure, { once: true });
  document.fonts?.ready.then(measure);

  if ("IntersectionObserver" in window) {
    const revealTargets = document.querySelectorAll(
      ".section-top > *, .feature, .app-showcase, .details-copy, .detail-list article, .download-card, .faq-grid > *, .community-inner > *"
    );
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      }
    }, { threshold: .08, rootMargin: "0px 0px -30px 0px" });
    for (const element of revealTargets) {
      element.dataset.reveal = "";
      const siblings = Array.from(element.parentElement.children);
      element.style.setProperty("--reveal-delay", `${Math.min(siblings.indexOf(element), 2) * 85}ms`);
      observer.observe(element);
    }
    document.addEventListener("focusin", event => {
      for (let element = event.target; element; element = element.parentElement) {
        if (element.hasAttribute("data-reveal")) element.classList.add("is-visible");
      }
    });
  }

  function syncMotion() {
    const enabled = motionAllowed();
    document.documentElement.classList.toggle("motion-enabled", enabled);
    document.documentElement.dataset.motion = enabled ? "on" : "off";
    motionToggle.hidden = false;
    motionToggle.disabled = preference.matches;
    motionToggle.setAttribute("aria-pressed", String(enabled));
    motionToggle.querySelector("span").textContent = preference.matches ? "Reduced motion" : enabled ? "Motion on" : "Motion off";
    motionToggle.title = preference.matches ? "Animations reduced by your system preference" : "Toggle page animations and parallax";
    if (!enabled) {
      for (const element of document.querySelectorAll("[data-reveal]")) element.classList.add("is-visible");
      document.getAnimations().forEach(animation => animation.cancel());
    }
    document.dispatchEvent(new Event("motionchange"));
    schedule();
  }
  motionToggle.addEventListener("click", () => { userMotion = !userMotion; syncMotion(); });
  preference.addEventListener("change", syncMotion);
  syncMotion();
  measure();

  // A scripted visual explanation, never a simulated live connection or recording.
  const consoleElement = document.querySelector(".radio-console");
  const radioMessage = document.getElementById("radio-message");
  const radioChannel = document.getElementById("radio-channel");
  const radioCaption = document.getElementById("radio-caption");
  const radioPlay = document.getElementById("radio-play");
  const stepButtons = Array.from(document.querySelectorAll("[data-radio-step]"));
  const walkthrough = [
    { channel: "01 / PRESS TO TALK", message: "Hold a button. Keep your hands on the HOTAS.", caption: "A keyboard key or joystick button starts recording in the application." },
    { channel: "02 / YOUR QUESTION", message: "“Can you walk me through this checklist?”", caption: "Release PTT. Your voice is transcribed, locally or by your chosen service." },
    { channel: "03 / THE RIGHT CONTEXT", message: "Your aircraft. Your flight. Your manual passages.", caption: "Available DCS data and relevant indexed passages accompany the question." },
    { channel: "04 / YOUR COPILOT", message: "A short answer in your headset. The next step when you’re ready.", caption: "Text streams into the overlay. Speech follows phrase by phrase." },
  ];
  let radioStep = 0;
  let timer;
  let playing = false;

  function showStep(index) {
    radioStep = index;
    const step = walkthrough[index];
    radioMessage.textContent = step.message;
    radioChannel.textContent = step.channel;
    radioCaption.textContent = step.caption;
    consoleElement.style.setProperty("--radio-progress", (index + 1) / walkthrough.length);
    stepButtons.forEach((button, i) => button.setAttribute("aria-pressed", String(i === index)));
    if (motionAllowed() && radioMessage.animate) {
      radioMessage.getAnimations().forEach(animation => animation.cancel());
      radioMessage.animate([{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "translateY(0)" }], { duration: 450, easing: "ease-out" });
    }
  }

  function stopWalkthrough() {
    clearTimeout(timer);
    playing = false;
    consoleElement.classList.remove("is-playing");
    radioPlay.textContent = "Replay interaction ↻";
  }

  function advance() {
    if (!playing) return;
    if (radioStep === walkthrough.length - 1) { stopWalkthrough(); return; }
    showStep(radioStep + 1);
    timer = setTimeout(advance, 3200);
  }

  radioPlay.addEventListener("click", () => {
    if (playing) { stopWalkthrough(); return; }
    playing = true;
    showStep(0);
    consoleElement.classList.add("is-playing");
    radioPlay.textContent = "Pause preview Ⅱ";
    timer = setTimeout(advance, 2800);
  });
  stepButtons.forEach((button, index) => button.addEventListener("click", () => {
    stopWalkthrough();
    showStep(index);
  }));
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      cancelAnimationFrame(frame);
      frame = 0;
      if (playing) stopWalkthrough();
    } else schedule();
  });
  preference.addEventListener("change", () => { if (playing) stopWalkthrough(); });
})();
