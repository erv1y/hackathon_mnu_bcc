// Общие примитивы: материалы, скруглённые формы и мелкие утилиты.
import * as THREE from "../vendor/three.module.min.js";
import { RoundedBoxGeometry } from "../vendor/RoundedBoxGeometry.js";

export { THREE };

const geometries = new Map();
const materials = new Map();

function cachedGeometry(key, make) {
  if (!geometries.has(key)) geometries.set(key, make());
  return geometries.get(key);
}

export function mat(color, options = {}) {
  const { emissive = 0x000000, glow = 0, roughness = 0.82, metalness = 0, opacity = 1, own = false } = options;
  const key = [new THREE.Color(color).getHexString(), new THREE.Color(emissive).getHexString(), glow, roughness, metalness, opacity].join("|");
  if (!own && materials.has(key)) return materials.get(key);
  const material = new THREE.MeshStandardMaterial({
    color, emissive, emissiveIntensity: glow, roughness, metalness,
    transparent: opacity < 1, opacity, depthWrite: opacity >= 1,
  });
  if (!own) materials.set(key, material);
  return material;
}

function mesh(geometry, material, shadow = true) {
  const node = new THREE.Mesh(geometry, material);
  node.castShadow = shadow;
  node.receiveShadow = true;
  return node;
}

const asMaterial = (color, options) => (color && color.isMaterial ? color : mat(color, options));

export function rbox(w, h, d, color, radius = 0.05, options) {
  const r = Math.min(radius, w / 2.01, h / 2.01, d / 2.01);
  const geometry = cachedGeometry(`rb:${w}:${h}:${d}:${r}`, () => new RoundedBoxGeometry(w, h, d, 3, r));
  return mesh(geometry, asMaterial(color, options));
}

export function cyl(rTop, rBottom, h, color, segments = 24, options) {
  const geometry = cachedGeometry(`cy:${rTop}:${rBottom}:${h}:${segments}`, () => new THREE.CylinderGeometry(rTop, rBottom, h, segments));
  return mesh(geometry, asMaterial(color, options));
}

export function sphere(r, color, options, segments = 20) {
  const geometry = cachedGeometry(`sp:${r}:${segments}`, () => new THREE.SphereGeometry(r, segments, Math.round(segments * 0.75)));
  return mesh(geometry, asMaterial(color, options));
}

export function capsule(r, length, color, options) {
  const geometry = cachedGeometry(`ca:${r}:${length}`, () => new THREE.CapsuleGeometry(r, length, 6, 16));
  return mesh(geometry, asMaterial(color, options));
}

export function torus(r, tube, color, arc = Math.PI * 2, options) {
  const geometry = cachedGeometry(`to:${r}:${tube}:${arc}`, () => new THREE.TorusGeometry(r, tube, 10, 28, arc));
  return mesh(geometry, asMaterial(color, options));
}

export function plane(w, h, material) {
  const geometry = cachedGeometry(`pl:${w}:${h}`, () => new THREE.PlaneGeometry(w, h));
  return mesh(geometry, material, false);
}

export function place(node, x = 0, y = 0, z = 0, rotY = 0) {
  node.position.set(x, y, z);
  node.rotation.y = rotY;
  return node;
}

export function group(...children) {
  const node = new THREE.Group();
  children.forEach((child) => child && node.add(child));
  return node;
}

export const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
export const lerp = (a, b, t) => a + (b - a) * t;
export const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const smooth = (current, target, dt, speed = 8) => current + (target - current) * (1 - Math.exp(-speed * dt));
export const pick = (list) => list[Math.floor(Math.random() * list.length)];
export const rand = (min, max) => min + Math.random() * (max - min);

export function angleTo(current, target, dt, speed = 9) {
  let delta = target - current;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  return current + delta * (1 - Math.exp(-speed * dt));
}
