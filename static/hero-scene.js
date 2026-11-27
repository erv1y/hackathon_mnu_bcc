"use strict";

function initHeroScene() {
  const scene = document.getElementById("hero-art");
  const clamp = (value, limit) => Math.min(limit, Math.max(-limit, value));
  let drag = null;
  let angle = { x: 0, y: 0 };

  function rotate(x, y) {
    angle = { x: clamp(x, 20), y: clamp(y, 44) };
    scene.style.setProperty("--tilt-x", `${angle.x}deg`);
    scene.style.setProperty("--tilt-y", `${angle.y}deg`);
  }

  scene.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, angle };
    scene.setPointerCapture(event.pointerId);
  });
  scene.addEventListener("pointermove", (event) => {
    if (drag) {
      rotate(drag.angle.x + (drag.y - event.clientY) / 5, drag.angle.y + (event.clientX - drag.x) / 4);
    } else if (event.pointerType === "mouse") {
      const bounds = scene.getBoundingClientRect();
      rotate((.5 - (event.clientY - bounds.top) / bounds.height) * 24,
        ((event.clientX - bounds.left) / bounds.width - .5) * 50);
    }
  });
  scene.addEventListener("lostpointercapture", () => { drag = null; });
  scene.addEventListener("pointerleave", (event) => { if (!drag && event.pointerType === "mouse") rotate(0, 0); });
  scene.addEventListener("keydown", (event) => {
    const steps = { ArrowLeft: [0, -8], ArrowRight: [0, 8], ArrowUp: [6, 0], ArrowDown: [-6, 0] };
    if (event.key === "Home") rotate(0, 0);
    else if (Object.hasOwn(steps, event.key)) rotate(angle.x + steps[event.key][0], angle.y + steps[event.key][1]);
    else return;
    event.preventDefault();
  });
}
