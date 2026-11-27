// Цвета сцены берутся из CSS-переменных бренда (style.css :root).
import { THREE } from "./kit.js";

function cssColor(styles, name, fallback) {
  const value = styles.getPropertyValue(name).trim();
  try { return new THREE.Color(value || fallback); } catch (_) { return new THREE.Color(fallback); }
}

const mix = (a, b, t) => a.clone().lerp(b, t);

export function readPalette() {
  const styles = getComputedStyle(document.documentElement);
  const bg = cssColor(styles, "--bg", "#faf9fc");
  const accent = cssColor(styles, "--accent", "#6826b9");
  const action = cssColor(styles, "--action", "#8934f9");
  const heroBase = cssColor(styles, "--hero-base", "#17131f");
  const heroLight = cssColor(styles, "--hero-light", "#392755");
  const surface = cssColor(styles, "--surface", "#f2eff6");
  const border = cssColor(styles, "--border", "#e7e0ed");
  const border2 = cssColor(styles, "--border2", "#d1c4dd");
  const glass2 = cssColor(styles, "--glass2", "#f5efff");
  const low = cssColor(styles, "--low-text", "#17674c");
  const med = cssColor(styles, "--med-text", "#89510a");
  const high = cssColor(styles, "--high-text", "#a72b3a");
  const white = new THREE.Color("#ffffff");

  return {
    bg, accent, action, heroBase, heroLight, surface, border, border2, glass2, low, med, high, white,
    floorOffice: mix(surface, border, 0.35),
    floorHome: mix(new THREE.Color("#efe4d8"), surface, 0.45),
    wall: mix(bg, glass2, 0.6),
    wallHome: mix(new THREE.Color("#f6eee6"), glass2, 0.35),
    trim: border2,
    wood: mix(new THREE.Color("#d9c2a7"), surface, 0.25),
    woodDark: mix(new THREE.Color("#a98b73"), heroLight, 0.12),
    desk: mix(white, surface, 0.55),
    metal: mix(border2, heroLight, 0.25),
    dark: mix(heroBase, heroLight, 0.35),
    screen: mix(heroBase, accent, 0.18),
    soft: mix(action, white, 0.72),
    softer: mix(action, white, 0.86),
    accentSoft: mix(accent, white, 0.45),
    mint: mix(low, white, 0.62),
    peach: mix(med, white, 0.66),
    rose: mix(high, white, 0.66),
    sky: mix(new THREE.Color("#4f86c6"), white, 0.55),
    plant: mix(low, new THREE.Color("#7fb48d"), 0.6),
    plantPot: mix(new THREE.Color("#e9d8cc"), border2, 0.3),
    skin: [new THREE.Color("#f3d2b8"), new THREE.Color("#e3b58f"), new THREE.Color("#c99068"), new THREE.Color("#f6dcc7")],
    hair: [mix(heroBase, heroLight, 0.2), new THREE.Color("#5b3c2a"), new THREE.Color("#2c2433"), new THREE.Color("#8a5a3b")],
  };
}
