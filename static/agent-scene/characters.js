// Фабрика мини-человечков из примитивов и процедурная анимация без скелета.
import { THREE, mat, rbox, sphere, capsule, torus, group, place, clamp } from "./kit.js";

const LEG = 0.3;
export const SEAT_DROP = LEG;

function hairCap(radius, coverage, color) {
  const geometry = new THREE.SphereGeometry(radius, 22, 16, 0, Math.PI * 2, 0, Math.PI * coverage);
  const node = new THREE.Mesh(geometry, color);
  node.castShadow = true;
  return node;
}

function makeHair(style, material) {
  const hair = new THREE.Group();
  const cap = hairCap(0.252, style === "bob" ? 0.6 : 0.5, material);
  cap.rotation.x = -0.35;
  hair.add(cap);
  if (style === "bun") hair.add(place(sphere(0.1, material), 0, 0.2, -0.12));
  if (style === "bob") {
    hair.add(place(sphere(0.12, material), 0.17, -0.06, -0.05));
    hair.add(place(sphere(0.12, material), -0.17, -0.06, -0.05));
  }
  if (style === "curly") {
    for (let i = 0; i < 7; i += 1) {
      const angle = (i / 7) * Math.PI * 2;
      hair.add(place(sphere(0.075, material), Math.cos(angle) * 0.17, 0.17, Math.sin(angle) * 0.17 - 0.03));
    }
  }
  return hair;
}

export function createCharacter(config, palette) {
  const o = config.outfit;
  const skin = mat(palette.skin[o.skin % palette.skin.length], { own: true });
  const top = mat(o.top, { own: true });
  const shirt = mat(o.shirt, { own: true });
  const pants = mat(o.pants, { own: true });
  const hairMat = mat(palette.hair[o.hair % palette.hair.length], { own: true, roughness: 0.7 });
  const dark = mat(palette.heroBase);
  const shoe = mat(palette.dark);
  const blush = mat(palette.rose, { opacity: 0.75 });
  const glowable = [top, shirt, pants, skin, hairMat];

  const root = new THREE.Group();
  const body = new THREE.Group();
  root.add(body);
  const hips = place(new THREE.Group(), 0, LEG, 0);
  body.add(hips);

  // Ноги
  const legs = [0.085, -0.085].map((x) => {
    const pivot = place(new THREE.Group(), x, LEG, 0);
    pivot.add(place(capsule(0.07, 0.13, pants), 0, -0.13, 0));
    pivot.add(place(rbox(0.12, 0.08, 0.18, shoe, 0.035), 0, -0.27, 0.03));
    body.add(pivot);
    return pivot;
  });

  // Корпус и одежда по стилю
  const torsoColor = o.style === "vest" ? shirt : top;
  hips.add(place(capsule(0.175, 0.2, torsoColor), 0, 0.26, 0));
  if (o.style === "blazer" || o.style === "cardigan") {
    hips.add(place(rbox(o.style === "blazer" ? 0.13 : 0.09, 0.22, 0.05, shirt, 0.02), 0, 0.31, 0.15));
    if (o.tie) hips.add(place(rbox(0.04, 0.16, 0.03, mat(o.tie), 0.012), 0, 0.3, 0.177));
  }
  if (o.style === "vest") {
    hips.add(place(rbox(0.1, 0.27, 0.05, top, 0.02), 0.075, 0.25, 0.14));
    hips.add(place(rbox(0.1, 0.27, 0.05, top, 0.02), -0.075, 0.25, 0.14));
  }
  if (o.style === "sweater") {
    const collar = torus(0.09, 0.025, shirt);
    collar.rotation.x = Math.PI / 2;
    hips.add(place(collar, 0, 0.5, 0.02));
  }
  if (o.style === "hoodie") {
    const hood = torus(0.13, 0.05, top, Math.PI * 1.2);
    hood.rotation.set(Math.PI / 2 + 0.3, 0, Math.PI * 0.9);
    hips.add(place(hood, 0, 0.5, -0.04));
    hips.add(place(rbox(0.18, 0.08, 0.04, mat(palette.accentSoft), 0.02), 0, 0.12, 0.16));
  }
  if (o.lanyard) {
    const cord = torus(0.12, 0.008, mat(palette.action), Math.PI);
    cord.rotation.set(-0.35, 0, Math.PI);
    hips.add(place(cord, 0, 0.47, 0.08));
    hips.add(place(rbox(0.06, 0.08, 0.012, mat(palette.white), 0.01), 0, 0.33, 0.18));
  }
  if (o.badge) hips.add(place(rbox(0.05, 0.035, 0.015, mat("#e8c46a", { metalness: 0.4, roughness: 0.4 }), 0.008), 0.08, 0.36, 0.165));
  let glowBadge = null;
  if (o.glowBadge) {
    glowBadge = rbox(0.07, 0.07, 0.02, mat(palette.action, { emissive: palette.action, glow: 1.4, own: true }), 0.015);
    hips.add(place(glowBadge, 0.075, 0.36, 0.17));
  }

  // Голова
  const head = place(new THREE.Group(), 0, 0.66, 0);
  hips.add(head);
  head.add(sphere(0.235, skin, undefined, 24));
  const hair = makeHair(o.hairStyle, hairMat);
  hair.position.y = 0.02;
  head.add(hair);
  const eyes = [0.08, -0.08].map((x) => {
    const eye = place(sphere(0.03, dark), x, 0.0, 0.212);
    head.add(eye);
    return eye;
  });
  [0.135, -0.135].forEach((x) => {
    const cheek = place(sphere(0.035, blush), x, -0.06, 0.185);
    cheek.scale.set(1, 0.6, 0.4);
    head.add(cheek);
  });
  const mouth = torus(0.035, 0.009, dark, Math.PI);
  mouth.rotation.z = Math.PI;
  head.add(place(mouth, 0, -0.055, 0.218));
  if (o.glasses) {
    const frame = mat(palette.heroBase, { roughness: 0.4 });
    [0.08, -0.08].forEach((x) => head.add(place(torus(0.05, 0.009, frame), x, 0, 0.225)));
    head.add(place(rbox(0.06, 0.012, 0.012, frame, 0.004), 0, 0.01, 0.228));
  }

  // Руки
  const arms = [0.215, -0.215].map((x) => {
    const pivot = place(new THREE.Group(), x, 0.43, 0);
    pivot.add(place(capsule(0.055, 0.17, top), 0, -0.13, 0));
    const hand = place(sphere(0.06, skin), 0, -0.27, 0);
    pivot.add(hand);
    hips.add(pivot);
    return { pivot, hand };
  });

  // Точка, где персонаж держит документ, геймпад и т. п.
  const hold = place(new THREE.Group(), 0, 0.22, 0.3);
  hips.add(hold);

  // Подсветка активного агента — мягкое кольцо на полу
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.34, 0.46, 40),
    new THREE.MeshBasicMaterial({ color: palette.action, transparent: true, opacity: 0, depthWrite: false }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.015;
  root.add(ring);

  root.traverse((node) => { if (node.isMesh) node.userData.actorKey = config.key; });

  const character = {
    root, body, hips, head, legs, arms, hold, ring, glowBadge,
    handR: arms[1].hand, handL: arms[0].hand,
    blinkTimer: 1 + Math.random() * 3,
    highlight: 0,
    setHighlight(value) {
      if (Math.abs(value - this.highlight) < 0.001) return;
      this.highlight = value;
      glowable.forEach((material) => {
        material.emissive.copy(palette.action);
        material.emissiveIntensity = value * 0.22;
      });
    },
  };
  return character;
}

// a — веса анимаций (0..1), t — время, phase — фаза шага.
export function poseCharacter(c, a, t, phase, dt) {
  const walk = a.walk || 0;
  const sit = a.sit || 0;
  const swing = Math.sin(phase);

  c.legs[0].rotation.x = swing * 0.6 * walk - (Math.PI / 2) * sit;
  c.legs[1].rotation.x = -swing * 0.6 * walk - (Math.PI / 2) * sit;

  let lx = -swing * 0.5 * walk, rx = swing * 0.5 * walk;
  let lz = 0.06, rz = -0.06;
  let headX = 0, headZ = 0;
  let bodyY = Math.abs(Math.cos(phase)) * 0.04 * walk;
  let lean = 0.05 * walk;

  const type = a.type || 0;
  if (type) {
    lx += (-1.15 + Math.sin(t * 17) * 0.09) * type;
    rx += (-1.15 + Math.sin(t * 17 + 2) * 0.09) * type;
    lz -= 0.18 * type; rz += 0.18 * type;
    headX += 0.14 * type; lean += 0.08 * type;
  }
  const read = a.read || 0;
  if (read) {
    lx += -1.05 * read; rx += -1.05 * read;
    lz -= 0.32 * read; rz += 0.32 * read;
    headX += 0.24 * read + Math.sin(t * 0.9) * 0.04 * read;
  }
  const game = a.game || 0;
  if (game) {
    lx += (-0.95 + Math.sin(t * 11) * 0.05) * game;
    rx += (-0.95 + Math.sin(t * 13 + 1) * 0.05) * game;
    lz -= 0.36 * game; rz += 0.36 * game;
    headZ += Math.sin(t * 1.3) * 0.06 * game;
  }
  const drink = a.drink || 0;
  if (drink) {
    const sip = Math.pow((Math.sin(t * 1.4) + 1) / 2, 2);
    rx += (-0.5 - 1.75 * sip) * drink;
    rz += 0.35 * drink;
    headX -= 0.12 * sip * drink;
  }
  const talk = a.talk || 0;
  if (talk) {
    headX += Math.sin(t * 5) * 0.07 * talk;
    lx += (-0.45 + Math.sin(t * 3.1) * 0.3) * talk;
    lz += 0.15 * talk;
  }
  const gesture = a.gesture || 0;
  if (gesture) {
    rx += (-2.0 + Math.sin(t * 2.2) * 0.25) * gesture;
    rz -= 0.1 * gesture;
    headX -= 0.15 * gesture;
  }
  const shrug = a.shrug || 0;
  if (shrug) {
    lz += 0.85 * shrug; rz -= 0.85 * shrug;
    lx -= 0.35 * shrug; rx -= 0.35 * shrug;
    headZ += 0.14 * shrug;
    bodyY += 0.03 * shrug;
  }
  const cheer = a.cheer || 0;
  if (cheer) {
    lx += -2.8 * cheer; rx += -2.8 * cheer;
    lz += 0.25 * cheer; rz -= 0.25 * cheer;
    bodyY += Math.abs(Math.sin(t * 7)) * 0.1 * cheer * (1 - sit);
    headX -= 0.15 * cheer;
  }
  const reach = a.reach || 0;
  if (reach) {
    rx += -0.9 * reach;
    rz -= 0.75 * reach;
  }
  const carry = a.carry || 0;
  if (carry) {
    lx = lx * (1 - carry) - 0.95 * carry;
    rx = rx * (1 - carry) - 0.95 * carry;
    lz -= 0.28 * carry; rz += 0.28 * carry;
  }
  const leanBack = a.lean || 0;
  lean -= 0.18 * leanBack;

  c.arms[0].pivot.rotation.set(lx, 0, lz);
  c.arms[1].pivot.rotation.set(rx, 0, rz);
  c.head.rotation.x = headX + Math.sin(t * 1.7) * 0.02;
  c.head.rotation.z = headZ;
  c.head.rotation.y = a.look || 0;
  c.hips.rotation.x = lean;
  c.body.position.y = bodyY + Math.sin(t * 2.1) * 0.006;

  // Моргание
  c.blinkTimer -= dt;
  const closed = c.blinkTimer < 0.12;
  if (c.blinkTimer < 0) c.blinkTimer = 2 + Math.random() * 3.5;
  c.head.children.forEach((child) => {
    if (child.geometry?.parameters?.radius === 0.03) child.scale.y = closed ? 0.15 : 1;
  });

  if (c.glowBadge) c.glowBadge.material.emissiveIntensity = 1.0 + Math.sin(t * 3) * 0.45;
  c.ring.material.opacity = clamp(a.active || 0) * (0.45 + Math.sin(t * 4) * 0.15);
}
