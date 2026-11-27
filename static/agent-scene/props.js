// Мебель и мелкие предметы — процедурно, низкополигонально, со скруглёнными формами.
import { THREE, mat, rbox, cyl, sphere, torus, group, place, plane } from "./kit.js";

export function desk(p, w = 1.3, d = 0.68) {
  const top = place(rbox(w, 0.06, d, p.desk, 0.025), 0, 0.57, 0);
  const legs = [[1, 1], [1, -1], [-1, 1], [-1, -1]].map(([sx, sz]) =>
    place(cyl(0.025, 0.025, 0.55, p.metal, 10), sx * (w / 2 - 0.08), 0.275, sz * (d / 2 - 0.08)));
  const drawer = place(rbox(0.36, 0.32, d - 0.1, p.surface, 0.03), w / 2 - 0.26, 0.38, 0);
  return group(top, ...legs, drawer);
}

export function chair(p, color) {
  const seat = place(rbox(0.46, 0.08, 0.44, color, 0.04), 0, 0.33, 0);
  const back = place(rbox(0.46, 0.42, 0.07, color, 0.04), 0, 0.6, -0.21);
  const stem = place(cyl(0.03, 0.03, 0.26, p.metal, 10), 0, 0.17, 0);
  const foot = place(cyl(0.2, 0.22, 0.04, p.metal, 18), 0, 0.03, 0);
  return group(seat, back, stem, foot);
}

export function monitor(p) {
  const screenMat = mat(p.screen, { emissive: p.accent, glow: 0.35, own: true });
  const screen = place(rbox(0.56, 0.34, 0.04, screenMat, 0.02), 0, 0.98, 0);
  const frame = place(rbox(0.6, 0.38, 0.03, p.dark, 0.025), 0, 0.98, -0.025);
  const neck = place(cyl(0.025, 0.025, 0.2, p.metal, 10), 0, 0.72, -0.03);
  const base = place(rbox(0.22, 0.02, 0.14, p.metal, 0.01), 0, 0.61, -0.03);
  const lines = [0.08, 0.02, -0.04].map((y, i) => place(rbox(0.3 - i * 0.06, 0.018, 0.005, mat(p.soft, { emissive: p.soft, glow: 0.6 }), 0.005), -0.06 + i * 0.03, 0.98 + y, 0.022));
  const node = group(frame, screen, neck, base, ...lines);
  node.userData.screen = screenMat;
  return node;
}

export function laptop(p, open = 1) {
  const base = place(rbox(0.38, 0.025, 0.26, p.metal, 0.012), 0, 0.012, 0);
  const lidPivot = place(new THREE.Group(), 0, 0.025, -0.13);
  const screenMat = mat(p.screen, { emissive: p.action, glow: 0.0, own: true });
  lidPivot.add(place(rbox(0.38, 0.24, 0.018, p.metal, 0.012), 0, 0.12, 0));
  lidPivot.add(place(rbox(0.34, 0.2, 0.004, screenMat, 0.004), 0, 0.12, 0.011));
  lidPivot.add(place(sphere(0.012, mat(p.white, { emissive: p.white, glow: 0.5 })), 0, 0.12, -0.011));
  const keys = place(rbox(0.32, 0.006, 0.12, p.dark, 0.003), 0, 0.027, 0.02);
  const node = group(base, keys, lidPivot);
  node.userData.lid = lidPivot;
  node.userData.screen = screenMat;
  setLaptopOpen(node, open);
  return node;
}

export function setLaptopOpen(node, open) {
  node.userData.lid.rotation.x = -(1 - open) * 1.62 + 0.18 * open;
  node.userData.screen.emissiveIntensity = open * 0.9;
}

export function printer(p) {
  const body = place(rbox(0.5, 0.24, 0.4, p.white, 0.05), 0, 0.12, 0);
  const top = place(rbox(0.44, 0.04, 0.3, p.border2, 0.02), 0, 0.25, -0.02);
  const tray = place(rbox(0.32, 0.015, 0.16, p.border2, 0.006), 0, 0.09, 0.26);
  const light = place(sphere(0.018, mat(p.low, { emissive: p.low, glow: 0.9, own: true })), 0.17, 0.2, 0.2);
  const node = group(body, top, tray, light);
  node.userData.light = light.material;
  return node;
}

export function cabinet(p, w = 0.6, h = 0.62, d = 0.45, color = p.surface) {
  const body = place(rbox(w, h, d, color, 0.04), 0, h / 2, 0);
  const handles = [h * 0.3, h * 0.7].map((y) => place(rbox(0.16, 0.025, 0.02, p.metal, 0.01), 0, y, d / 2 + 0.005));
  return group(body, ...handles);
}

export function plant(p, scale = 1) {
  const pot = place(cyl(0.17, 0.13, 0.3, p.plantPot, 18), 0, 0.15, 0);
  const leaves = [[0, 0.5, 0, 0.2], [0.1, 0.62, 0.05, 0.15], [-0.1, 0.6, -0.04, 0.16], [0.02, 0.75, -0.02, 0.12]]
    .map(([x, y, z, r]) => place(sphere(r, p.plant), x, y, z));
  const node = group(pot, ...leaves);
  node.scale.setScalar(scale);
  return node;
}

export function mug(p, color = p.white) {
  const cup = place(cyl(0.045, 0.04, 0.09, color, 14), 0, 0.045, 0);
  const handle = place(torus(0.025, 0.008, color), 0.05, 0.05, 0);
  return group(cup, handle);
}

export function coffeeMachine(p) {
  const body = place(rbox(0.42, 0.5, 0.36, p.dark, 0.05), 0, 0.25, 0);
  const top = place(rbox(0.38, 0.08, 0.3, p.metal, 0.03), 0, 0.53, -0.02);
  const spout = place(rbox(0.12, 0.06, 0.1, p.metal, 0.02), 0, 0.33, 0.18);
  const drip = place(rbox(0.26, 0.03, 0.14, p.metal, 0.01), 0, 0.04, 0.2);
  const light = place(sphere(0.02, mat(p.action, { emissive: p.action, glow: 1 })), 0.13, 0.44, 0.185);
  return group(body, top, spout, drip, light, place(mug(p, p.soft), 0, 0.06, 0.2));
}

export function counter(p, w, d = 0.6, color = p.desk) {
  const body = place(rbox(w, 0.8, d, p.surface, 0.04), 0, 0.4, 0);
  const top = place(rbox(w + 0.04, 0.05, d + 0.04, color, 0.02), 0, 0.82, 0);
  return group(body, top);
}

export function smartBoard(p) {
  const frame = place(rbox(1.7, 1.0, 0.06, p.dark, 0.04), 0, 0, 0);
  const screen = place(rbox(1.58, 0.88, 0.02, mat(p.white, { emissive: p.softer, glow: 0.35 }), 0.02), 0, 0, 0.035);
  const bars = [0.25, 0.4, 0.18, 0.32].map((h, i) =>
    place(rbox(0.12, h, 0.01, mat(i % 2 ? p.action : p.accentSoft), 0.01), -0.55 + i * 0.18, -0.3 + h / 2, 0.05));
  const notes = [[p.peach, 0.25, 0.2], [p.mint, 0.48, 0.24], [p.soft, 0.36, -0.05], [p.rose, 0.6, -0.02]]
    .map(([color, x, y]) => place(rbox(0.15, 0.15, 0.01, mat(color), 0.01), x, y, 0.05));
  const line = place(rbox(0.7, 0.025, 0.01, mat(p.accent), 0.01), -0.35, 0.28, 0.05);
  return group(frame, screen, ...bars, ...notes, line);
}

export function roundTable(p, h = 0.95) {
  const top = place(cyl(0.38, 0.38, 0.05, p.desk, 26), 0, h, 0);
  const stem = place(cyl(0.04, 0.04, h, p.metal, 10), 0, h / 2, 0);
  const foot = place(cyl(0.22, 0.24, 0.03, p.metal, 18), 0, 0.015, 0);
  return group(top, stem, foot);
}

export function sofa(p, color, w = 1.9) {
  const base = place(rbox(w, 0.3, 0.8, color, 0.08), 0, 0.2, 0);
  const back = place(rbox(w, 0.5, 0.22, color, 0.1), 0, 0.5, -0.32);
  const armL = place(rbox(0.22, 0.42, 0.8, color, 0.09), w / 2 - 0.08, 0.32, 0);
  const armR = place(rbox(0.22, 0.42, 0.8, color, 0.09), -w / 2 + 0.08, 0.32, 0);
  const cushions = [-0.42, 0.42].map((x) => place(rbox(0.78, 0.12, 0.6, p.softer, 0.06), x * (w / 1.9), 0.4, 0.04));
  const pillow = place(rbox(0.3, 0.3, 0.1, p.peach, 0.06), w / 2 - 0.38, 0.6, -0.16);
  pillow.rotation.z = 0.2;
  return group(base, back, armL, armR, ...cushions, pillow);
}

export function tv(p) {
  const screenMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  const frame = place(rbox(1.7, 0.98, 0.07, p.heroBase, 0.03), 0, 0, 0);
  const screen = place(plane(1.6, 0.88, screenMat), 0, 0, 0.037);
  const node = group(frame, screen);
  node.userData.screen = screenMat;
  return node;
}

export function tvStand(p) {
  const body = place(rbox(1.9, 0.42, 0.45, p.wood, 0.05), 0, 0.21, 0);
  const doors = [-0.47, 0.47].map((x) => place(rbox(0.86, 0.32, 0.02, p.desk, 0.02), x, 0.21, 0.23));
  const console = place(rbox(0.36, 0.07, 0.25, p.white, 0.03), 0.55, 0.455, 0);
  const consoleLight = place(rbox(0.2, 0.01, 0.005, mat(p.action, { emissive: p.action, glow: 1.2 }), 0.004), 0.55, 0.47, 0.127);
  return group(body, ...doors, console, consoleLight);
}

export function coffeeTable(p) {
  const top = place(rbox(1.0, 0.06, 0.56, p.wood, 0.04), 0, 0.36, 0);
  const legs = [[1, 1], [1, -1], [-1, 1], [-1, -1]].map(([sx, sz]) =>
    place(cyl(0.03, 0.025, 0.34, p.woodDark, 10), sx * 0.42, 0.17, sz * 0.2));
  return group(top, ...legs);
}

export function sideTable(p) {
  const top = place(cyl(0.24, 0.24, 0.04, p.wood, 22), 0, 0.48, 0);
  const stem = place(cyl(0.03, 0.03, 0.46, p.woodDark, 10), 0, 0.24, 0);
  const foot = place(cyl(0.15, 0.17, 0.03, p.woodDark, 16), 0, 0.015, 0);
  return group(top, stem, foot);
}

export function can(p) {
  const body = place(cyl(0.04, 0.04, 0.13, mat("#c8414b", { roughness: 0.45, metalness: 0.2 }), 14), 0, 0.065, 0);
  const band = place(cyl(0.041, 0.041, 0.03, p.white, 14), 0, 0.07, 0);
  const lid = place(cyl(0.035, 0.035, 0.01, p.metal, 14), 0, 0.135, 0);
  return group(body, band, lid);
}

export function phone(p) {
  const screenMat = mat(p.dark, { emissive: p.action, glow: 0, own: true });
  const body = place(rbox(0.09, 0.015, 0.17, p.heroBase, 0.012), 0, 0.008, 0);
  const screen = place(rbox(0.08, 0.004, 0.155, screenMat, 0.004), 0, 0.017, 0);
  const node = group(body, screen);
  node.userData.screen = screenMat;
  return node;
}

export function gamepad(p) {
  const body = place(rbox(0.2, 0.05, 0.1, p.white, 0.025), 0, 0, 0);
  const grips = [0.08, -0.08].map((x) => place(sphere(0.045, p.white), x, -0.005, -0.03));
  const buttons = [[0.05, p.action], [-0.05, p.dark]].map(([x, color]) => place(sphere(0.014, color), x, 0.03, 0.01));
  return group(body, ...grips, ...buttons);
}

export function shelf(p, w = 1.2, h = 1.5) {
  const frame = place(rbox(w, h, 0.34, p.wood, 0.03), 0, h / 2, 0);
  const node = group(frame);
  const colors = [p.accentSoft, p.mint, p.peach, p.sky, p.rose, p.soft];
  for (let row = 0; row < 3; row += 1) {
    const y = 0.18 + row * (h / 3.2);
    node.add(place(rbox(w - 0.08, 0.025, 0.3, p.desk, 0.01), 0, y, 0.03));
    let x = -w / 2 + 0.12;
    for (let i = 0; i < 6 && x < w / 2 - 0.15; i += 1) {
      const bw = 0.06 + ((row * 7 + i * 3) % 4) * 0.015;
      const bh = 0.2 + ((row + i) % 3) * 0.04;
      node.add(place(rbox(bw, bh, 0.22, mat(colors[(row * 2 + i) % colors.length]), 0.01), x, y + bh / 2 + 0.012, 0.05));
      x += bw + 0.02;
    }
  }
  return node;
}

export function lamp(p) {
  const base = place(cyl(0.15, 0.17, 0.03, p.dark, 18), 0, 0.015, 0);
  const pole = place(cyl(0.015, 0.015, 1.4, p.dark, 8), 0, 0.72, 0);
  const shade = place(cyl(0.14, 0.24, 0.26, mat(p.desk, { emissive: "#ffe8c4", glow: 0.55 }), 22), 0, 1.45, 0);
  return group(base, pole, shade);
}

export function rug(p, w, d, color) {
  return place(rbox(w, 0.02, d, color, 0.01), 0, 0.01, 0);
}

export function beanbag(p, color) {
  const node = place(sphere(0.42, color), 0, 0.26, 0);
  node.scale.set(1, 0.62, 1);
  return group(node);
}

export function fridge(p) {
  const body = place(rbox(0.7, 1.75, 0.62, p.white, 0.06), 0, 0.875, 0);
  const handle = place(rbox(0.03, 0.4, 0.03, p.metal, 0.01), 0.27, 1.1, 0.32);
  const handle2 = place(rbox(0.03, 0.25, 0.03, p.metal, 0.01), 0.27, 0.45, 0.32);
  const line = place(rbox(0.66, 0.012, 0.01, p.border2, 0.004), 0, 0.72, 0.312);
  return group(body, handle, handle2, line);
}

export function windowPanel(p, w = 1.3, h = 1.1) {
  const frame = place(rbox(0.06, h, w, p.white, 0.02), 0, 0, 0);
  const glass = place(rbox(0.02, h - 0.12, w - 0.12, mat(p.sky, { emissive: p.sky, glow: 0.35, opacity: 0.85 }), 0.01), 0.03, 0, 0);
  const bar = place(rbox(0.025, h - 0.1, 0.04, p.white, 0.01), 0.04, 0, 0);
  return group(frame, glass, bar);
}

export function wallFrame(p, color, w = 0.6, h = 0.45) {
  const frame = place(rbox(w, h, 0.04, p.woodDark, 0.02), 0, 0, 0);
  const art = place(rbox(w - 0.1, h - 0.1, 0.01, mat(color), 0.01), 0, 0, 0.022);
  const sun = place(sphere(0.06, mat(p.peach)), w * 0.18, h * 0.12, 0.03);
  sun.scale.z = 0.2;
  return group(frame, art, sun);
}

export function clock(p) {
  const face = cyl(0.2, 0.2, 0.04, p.white, 28);
  face.rotation.x = Math.PI / 2;
  const rim = torus(0.2, 0.02, p.dark);
  const hand = place(rbox(0.015, 0.13, 0.01, p.dark, 0.005), 0, 0.05, 0.03);
  const hand2 = place(rbox(0.1, 0.015, 0.01, p.accent, 0.005), 0.04, 0, 0.03);
  return group(face, rim, hand, hand2);
}

export function paper(p) {
  const sheet = rbox(0.22, 0.008, 0.3, mat(p.white, { emissive: p.action, glow: 0.18, own: true }), 0.004);
  const lines = [-0.08, -0.03, 0.02, 0.07].map((z, i) => place(rbox(i === 0 ? 0.12 : 0.16, 0.002, 0.012, mat(p.border2), 0.001), -0.01, 0.006, z));
  const halo = new THREE.Mesh(
    new THREE.PlaneGeometry(0.42, 0.5),
    new THREE.MeshBasicMaterial({ color: p.action, transparent: true, opacity: 0.22, depthWrite: false }),
  );
  halo.rotation.x = -Math.PI / 2;
  halo.position.y = -0.006;
  const node = group(sheet, ...lines, halo);
  node.userData.sheet = sheet.material;
  node.userData.halo = halo.material;
  return node;
}

export function folder(p, color) {
  return group(place(rbox(0.26, 0.03, 0.32, mat(color), 0.012), 0, 0.015, 0));
}

// Предметы на столах — отражают специализацию агента.
export function deskDecor(p, role) {
  const node = new THREE.Group();
  if (role === "payments") {
    node.add(place(rbox(0.12, 0.05, 0.18, p.dark, 0.02), 0.42, 0.625, 0.08));
    node.add(place(rbox(0.09, 0.004, 0.06, mat(p.mint, { emissive: p.mint, glow: 0.5 }), 0.002), 0.42, 0.652, 0.06));
    [p.action, p.peach, p.sky].forEach((color, i) => {
      const card = place(rbox(0.15, 0.008, 0.1, mat(color), 0.01), -0.42 + i * 0.03, 0.605 + i * 0.008, 0.12 - i * 0.02, 0.3 * i);
      node.add(card);
    });
  } else if (role === "kyc_aml") {
    [p.mint, p.sky, p.peach].forEach((color, i) => node.add(place(folder(p, color), -0.4, 0.6 + i * 0.032, 0.05, 0.1 * i)));
    node.add(place(rbox(0.2, 0.006, 0.26, p.white, 0.004), 0.4, 0.605, 0.1, -0.2));
    [0, 1, 2].forEach((i) => node.add(place(rbox(0.03, 0.006, 0.03, mat(p.low), 0.006), 0.34 + i * 0.012, 0.61, 0.02 + i * 0.07, -0.2)));
  } else if (role === "security") {
    const safe = place(rbox(0.26, 0.26, 0.24, p.metal, 0.04), -0.42, 0.73, 0.02);
    const dial = place(cyl(0.05, 0.05, 0.02, p.dark, 16), -0.42, 0.74, 0.15);
    dial.rotation.x = Math.PI / 2;
    const lockBody = place(rbox(0.12, 0.1, 0.05, mat("#e8c46a", { metalness: 0.4, roughness: 0.35 }), 0.02), 0.42, 0.65, 0.1);
    const shackle = place(torus(0.04, 0.012, p.metal, Math.PI), 0.42, 0.7, 0.1);
    node.add(safe, dial, lockBody, shackle);
  } else if (role === "logic") {
    const board = place(rbox(0.4, 0.3, 0.02, p.white, 0.015), -0.35, 0.78, 0.2, 0);
    board.rotation.x = -0.25;
    node.add(board);
    [[p.peach, -0.45, 0.82], [p.mint, -0.3, 0.84], [p.soft, -0.38, 0.72], [p.rose, -0.24, 0.73]].forEach(([color, x, y]) =>
      node.add(place(rbox(0.07, 0.07, 0.006, mat(color), 0.006), x, y, 0.21)));
    node.add(place(rbox(0.08, 0.08, 0.006, mat(p.peach), 0.006), 0.35, 0.604, -0.05, 0.4));
  } else if (role === "assembler") {
    node.add(place(folder(p, p.action), -0.4, 0.6, 0.05, 0.15));
    node.add(place(folder(p, p.accentSoft), -0.4, 0.632, 0.05, 0.05));
  }
  return node;
}
