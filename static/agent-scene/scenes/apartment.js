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
    ctx.fillStyle = "#d3e6da";
    ctx.fillRect(0, 0, 192, 55);
    ctx.fillStyle = "#edf0d6";
    ctx.beginPath();
    ctx.arc(143, 25, 12, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#728e72";
    ctx.fillRect(0, 53, 192, 55);
    for (let i = 0; i < 10; i += 1) {
      const x = i * 21 - 6;
      const h = 8 + (i * 13) % 21;
      ctx.fillStyle = i % 2 ? "#669185" : "#9aa991";
      ctx.fillRect(x, 53 - h, 12 + (i % 3) * 3, h);
    }
    const bend = Math.sin(t * .65) * 17;
    ctx.fillStyle = "#424d48";
    ctx.beginPath();
    ctx.moveTo(86 + bend, 53);
    ctx.lineTo(105 + bend, 53);
    ctx.lineTo(180, 108);
    ctx.lineTo(12, 108);
    ctx.fill();
    ctx.fillStyle = "#f5dda7";
    for (let i = 0; i < 5; i += 1) {
      const phase = (i / 5 + t * .66) % 1;
      const depth = phase * phase;
      ctx.fillRect(95 + bend * (1 - depth), 54 + depth * 48, 1 + depth * 3, 2 + depth * 7);
    }
    const carX = 95 + Math.sin(t * 1.1) * 10;
    ctx.fillStyle = "#203931";
    ctx.fillRect(carX - 11, 88, 22, 14);
    ctx.fillStyle = "#dc9567";
    ctx.fillRect(carX - 10, 91, 20, 8);
    ctx.fillStyle = "#b9d8d5";
    ctx.fillRect(carX - 7, 89, 14, 4);
    ctx.fillStyle = "#ffe0ae";
    ctx.fillRect(carX - 9, 97, 4, 2);
    ctx.fillRect(carX + 5, 97, 4, 2);
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
  const consoleBody = group(
    place(rbox(.29, .36, .18, p.white, .035), 0, .18, 0),
    place(rbox(.12, .34, .19, p.dark, .02), 0, .18, .01),
    place(rbox(.028, .13, .005, p.mint, .002), .03, .24, .11),
  );
  add(consoleBody, 3.13, .49, -2.56, tvYaw);
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
  const aisle = local(-1.17, .75);
  const look = local(-1.17, 1.34);
  const graph = new Graph({ sofa: [front.x, front.z], aisle: [aisle.x, aisle.z], window: [look.x, look.z] },
                          [["sofa", "aisle"], ["aisle", "window"]]);
  const seat = { node: "sofa", x: seatPos.x, z: seatPos.z, yaw: SOFA.yaw, h: 0.44 };

  return {
    root, graph, seat, laptop, phone, can, canRest, tvGlow, game,
    padRest: new THREE.Vector3(padRest.x, 0.42, padRest.z),
  };
}
