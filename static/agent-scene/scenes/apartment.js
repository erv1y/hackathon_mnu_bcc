// Быстрая проверка: уютная квартира — диван, ТВ с игрой, журнальный столик с MacBook.
import { THREE, rbox, sphere, cyl, place, group, mat } from "../kit.js";
import { Graph } from "../paths.js";
import * as P from "../props.js";

const SOFA = { x: -0.5, z: 0.5, yaw: (125 * Math.PI) / 180 };

function local(x, z) {
  const c = Math.cos(SOFA.yaw), s = Math.sin(SOFA.yaw);
  return { x: SOFA.x + x * c + z * s, z: SOFA.z - x * s + z * c };
}

function cat(p) {
  const fur = mat(p.peach);
  const body = place(sphere(0.2, fur), 0, 0.12, 0);
  body.scale.set(1.25, 0.6, 0.9);
  const head = place(sphere(0.11, fur), 0.24, 0.14, 0.05);
  const ears = [0.04, -0.04].map((dz) => {
    const ear = place(new THREE.Mesh(new THREE.ConeGeometry(0.035, 0.07, 8), fur), 0.26, 0.25, 0.05 + dz);
    ear.castShadow = true;
    return ear;
  });
  const tail = place(cyl(0.025, 0.02, 0.32, fur, 8), -0.24, 0.05, 0.12);
  tail.rotation.set(Math.PI / 2, 0, 0.9);
  return group(body, head, ...ears, tail);
}

function gameTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 192; canvas.height = 108;
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const ctx = canvas.getContext("2d");
  let last = -1;
  function draw(t, palette) {
    const frame = Math.floor(t * 14);
    if (frame === last) return;
    last = frame;
    const sky = ctx.createLinearGradient(0, 0, 0, 108);
    sky.addColorStop(0, "#" + palette.soft.getHexString());
    sky.addColorStop(1, "#" + palette.softer.getHexString());
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, 192, 108);
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < 3; i += 1) {
      const x = ((i * 80 - t * 12) % 240 + 240) % 240 - 30;
      ctx.beginPath(); ctx.ellipse(x, 20 + i * 9, 18, 7, 0, 0, Math.PI * 2); ctx.fill();
    }
    ctx.fillStyle = "#" + palette.plant.getHexString();
    ctx.fillRect(0, 84, 192, 24);
    ctx.fillStyle = "#" + palette.mint.getHexString();
    for (let i = 0; i < 6; i += 1) {
      const x = ((i * 44 - t * 40) % 264 + 264) % 264 - 30;
      ctx.fillRect(x, 70 - (i % 2) * 16, 26, 8);
    }
    ctx.fillStyle = "#f2c94c";
    for (let i = 0; i < 5; i += 1) {
      const x = ((i * 52 + 30 - t * 40) % 260 + 260) % 260 - 20;
      ctx.beginPath(); ctx.arc(x, 52 + Math.sin(t * 4 + i) * 4, 4, 0, Math.PI * 2); ctx.fill();
    }
    const jump = Math.max(0, Math.sin(t * 3.2)) * 30;
    ctx.fillStyle = "#" + palette.action.getHexString();
    ctx.fillRect(48, 66 - jump, 16, 18);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(56, 70 - jump, 4, 4);
    ctx.fillStyle = "rgba(23,19,31,.55)";
    ctx.fillRect(140, 6, 46, 12);
    ctx.fillStyle = "#ffffff";
    ctx.font = "bold 9px sans-serif";
    ctx.fillText(String(1200 + (frame % 900)).padStart(5, "0"), 146, 15);
    texture.needsUpdate = true;
  }
  return { texture, draw };
}

export function buildApartment(p) {
  const root = new THREE.Group();
  const add = (node, x, y, z, rot = 0) => { root.add(place(node, x, y, z, rot)); return node; };

  add(P.rug(p, 4.2, 3.0, p.softer), -0.2, 0, 0.0, SOFA.yaw);
  add(P.sofa(p, p.accentSoft), SOFA.x, 0, SOFA.z, SOFA.yaw);
  const tablePos = local(0, 1.05);
  add(P.coffeeTable(p), tablePos.x, 0, tablePos.z, SOFA.yaw);

  const laptopPos = local(-0.25, 1.0);
  const laptop = add(P.laptop(p, 0), laptopPos.x, 0.39, laptopPos.z, SOFA.yaw + Math.PI);
  const phonePos = local(0.3, 1.05);
  const phone = add(P.phone(p), phonePos.x, 0.39, phonePos.z, SOFA.yaw + 0.4);
  const padRest = local(0.05, 0.95);
  const sidePos = local(-1.3, 0.05);
  add(P.sideTable(p), sidePos.x, 0, sidePos.z);
  const can = add(P.can(p), sidePos.x, 0.5, sidePos.z);
  const canRest = new THREE.Vector3(sidePos.x, 0.5, sidePos.z);
  add(P.lamp(p), local(-1.35, -0.6).x, 0, local(-1.35, -0.6).z);

  // ТВ на тумбе, развернут к дивану.
  const tvYaw = (-18 * Math.PI) / 180;
  add(P.tvStand(p), 2.3, 0, -2.6, tvYaw);
  const tvNode = add(P.tv(p), 2.3, 1.0, -2.6, tvYaw);
  const game = gameTexture();
  tvNode.userData.screen.map = game.texture;
  tvNode.userData.screen.needsUpdate = true;
  const tvGlow = new THREE.PointLight(p.soft, 0.0, 4.5, 2);
  tvGlow.position.set(2.0, 1.0, -1.9);
  root.add(tvGlow);

  // Кухня, полки, декор.
  add(P.counter(p, 2.4, 0.62, p.wood), -5.6, 0, -2.3, Math.PI / 2);
  add(rbox(0.5, 0.04, 0.4, mat(p.metal), 0.02), -5.6, 0.85, -2.7);
  add(P.mug(p, p.soft), -5.55, 0.845, -1.6);
  add(P.plant(p, 0.6), -5.55, 0.845, -3.25);
  add(P.fridge(p), -5.55, 0, -0.4, Math.PI / 2);
  add(P.shelf(p, 1.4, 1.6), -2.4, 0, -4.23);
  add(P.wallFrame(p, p.sky), 0.3, 1.7, -4.4);
  add(P.wallFrame(p, p.mint, 0.42, 0.55), -0.65, 1.55, -4.4);
  add(P.plant(p, 1.2), 1.2, 0, -4.05);
  add(P.plant(p), 5.4, 0, -3.9);
  add(P.beanbag(p, p.mint), 1.6, 0, 2.9);
  // Обеденный уголок и рабочий стол — чтобы квартира не выглядела пустой.
  add(P.rug(p, 2.4, 2.4, p.peach), 4.0, 0, 1.6);
  add(P.roundTable(p, 0.62), 4.0, 0, 1.6);
  add(P.chair(p, p.mint), 3.3, 0, 1.6, Math.PI / 2);
  add(P.chair(p, p.sky), 4.7, 0, 1.6, -Math.PI / 2);
  add(P.mug(p, p.soft), 4.05, 0.645, 1.5);
  add(P.plant(p, 0.5), 3.9, 0.645, 1.75);
  add(P.desk(p, 1.3, 0.62), 4.6, 0, -3.95);
  add(P.chair(p, p.accentSoft), 4.6, 0, -3.25, Math.PI);
  add(P.lamp(p), 5.6, 0, -2.6);
  add(P.shelf(p, 0.9, 1.9), -4.4, 0, 3.9, Math.PI / 2);
  add(P.plant(p, 1.3), -5.4, 0, 3.6);
  add(P.wallFrame(p, p.rose, 0.8, 0.5), 4.6, 1.6, -4.4);
  add(cat(p), 0.9, 0.02, 1.8, 0.6);
  add(rbox(1.3, 0.05, 0.6, mat(p.wood), 0.02), -2.3, 0.02, 3.4, 0.2);

  const seatPos = local(-0.42, 0.08);
  const front = local(-0.42, 0.75);
  const graph = new Graph({ sofa: [front.x, front.z] }, []);
  const seat = { node: "sofa", x: seatPos.x, z: seatPos.z, yaw: SOFA.yaw, h: 0.44 };

  return {
    root, graph, seat, laptop, phone, can, canRest, tvGlow, game,
    padRest: new THREE.Vector3(padRest.x, 0.42, padRest.z),
  };
}
