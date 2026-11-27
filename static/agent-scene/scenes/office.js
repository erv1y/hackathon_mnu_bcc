// Глубокая проверка: маленький офис с рабочей зоной и зоной отдыха за перегородкой.
import { THREE, rbox, place, group, mat } from "../kit.js";
import { Graph } from "../paths.js";
import * as P from "../props.js";

const BACK_Z = -2.1;
const FRONT_Z = 1.5;
const SEAT_H = 0.37;

export function buildOffice(p) {
  const root = new THREE.Group();
  const add = (node, x, y, z, rot = 0) => { root.add(place(node, x, y, z, rot)); return node; };

  const desks = {};
  const monitors = [];
  const layout = [
    { key: "payments", x: -4.4, row: "back", chair: p.sky },
    { key: "kyc_aml", x: -1.8, row: "back", chair: p.mint },
    { key: "security", x: 0.8, row: "back", chair: p.heroLight },
    { key: "logic", x: 0.8, row: "front", chair: p.peach },
    { key: "assembler", x: -1.8, row: "front", chair: p.accentSoft },
  ];
  const seatNode = { payments: "h1", kyc_aml: "h2", security: "h3", logic: "h3", assembler: "h2" };

  for (const item of layout) {
    const back = item.row === "back";
    const z = back ? BACK_Z : FRONT_Z;
    const deskNode = group(P.desk(p), P.deskDecor(p, item.key));
    if (back) {
      const screen = P.monitor(p);
      screen.position.z = -0.16;
      deskNode.add(screen);
      monitors.push(screen.userData.screen);
    } else {
      const lap = P.laptop(p, 1);
      lap.position.set(0.02, 0.6, 0.02);
      deskNode.add(lap);
      monitors.push(lap.userData.screen);
    }
    deskNode.add(place(P.mug(p, item.chair), 0.22, 0.6, 0.18));
    add(deskNode, item.x, 0, z, back ? 0 : Math.PI);
    const seatZ = back ? -1.3 : 0.7;
    const chairNode = P.chair(p, item.chair);
    add(chairNode, item.x, 0, seatZ, back ? 0 : Math.PI);
    desks[item.key] = {
      seat: { node: seatNode[item.key], x: item.x, z: seatZ, yaw: back ? Math.PI : 0, h: SEAT_H },
      side: `${item.key}_side`,
      sideYaw: -Math.PI / 2,
      drop: new THREE.Vector3(item.x + 0.15, 0.62, back ? -1.82 : 1.22),
    };
  }

  // Принтер/факс у агента платежей — с него стартует документ.
  add(P.cabinet(p, 0.62, 0.6, 0.46), -5.45, 0, -1.75);
  const printer = add(P.printer(p), -5.45, 0.6, -1.75);
  const printerOut = new THREE.Vector3(-5.45, 0.71, -1.42);

  // Лоток готовых отчётов и принтер сборщика.
  add(P.cabinet(p, 1.0, 0.62, 0.46, p.desk), -3.3, 0, 1.55, Math.PI);
  add(P.printer(p), -3.05, 0.62, 1.55, Math.PI);
  const trayBox = rbox(0.34, 0.08, 0.3, mat(p.accentSoft), 0.02);
  add(trayBox, -3.6, 0.66, 1.5);
  const trayPos = new THREE.Vector3(-3.6, 0.72, 1.5);
  const trayGlow = new THREE.Mesh(
    new THREE.PlaneGeometry(0.6, 0.55),
    new THREE.MeshBasicMaterial({ color: p.low, transparent: true, opacity: 0, depthWrite: false }),
  );
  trayGlow.rotation.x = -Math.PI / 2;
  add(trayGlow, -3.6, 0.705, 1.5);

  // Перегородка с проходом.
  add(rbox(0.14, 1.0, 3.5, p.wall, 0.05), 2.0, 0.5, -2.75);
  add(rbox(0.14, 1.0, 4.1, p.wall, 0.05), 2.0, 0.5, 2.45);
  add(rbox(0.2, 0.06, 3.5, p.wood, 0.03), 2.0, 1.02, -2.75);
  add(rbox(0.2, 0.06, 4.1, p.wood, 0.03), 2.0, 1.02, 2.45);

  // Зона отдыха.
  add(P.counter(p, 1.7, 0.6), 5.0, 0, -4.1);
  add(P.coffeeMachine(p), 5.0, 0.845, -4.1);
  add(P.mug(p, p.peach), 5.55, 0.845, -4.0);
  add(P.mug(p, p.mint), 4.45, 0.845, -3.98);
  add(P.smartBoard(p), 3.25, 1.45, -4.38);
  add(P.roundTable(p), 4.15, 0, 1.4);
  add(P.rug(p, 3.2, 2.8, p.softer), 4.1, 0, 1.6);
  add(P.beanbag(p, p.peach), 5.3, 0, 3.4);
  add(P.plant(p, 1.2), 5.55, 0, -1.6);
  add(P.plant(p), 2.6, 0, 3.9);

  // Рабочая зона: декор.
  add(P.plant(p, 1.25), -5.4, 0, 3.8);
  add(P.cabinet(p, 0.62, 1.1, 0.46), -5.45, 0, 1.6, Math.PI / 2);
  add(P.plant(p, 0.9), -4.4, 0, 1.6);
  add(P.clock(p), -1.0, 1.9, -4.4);
  add(P.shelf(p, 1.3, 1.3), -3.4, 0, -4.22);
  add(P.rug(p, 6.8, 1.0, p.softer), -1.7, 0, -0.3);

  const graph = new Graph({
    h0: [-5.3, -0.3], fax: [-5.4, -0.95], h1: [-4.4, -0.3], h2: [-1.8, -0.3], h3: [0.8, -0.3],
    payments_side: [-3.8, -1.3], kyc_aml_side: [-1.2, -1.3], security_side: [1.4, -1.3],
    logic_side: [1.4, 0.7], assembler_side: [-1.2, 0.7], tray: [-3.6, 0.75],
    door: [2.0, -0.3], l0: [3.2, -0.3], l1: [4.7, -0.3],
    board: [3.25, -3.1], coffee: [5.0, -3.25], chatA: [3.55, 1.4], chatB: [4.75, 1.4],
  }, [
    ["h0", "h1"], ["h0", "fax"], ["h1", "h2"], ["h2", "h3"], ["h3", "door"], ["door", "l0"], ["l0", "l1"],
    ["h1", "payments_side"], ["h2", "kyc_aml_side"], ["h2", "assembler_side"], ["h2", "tray"], ["h1", "tray"],
    ["h3", "security_side"], ["h3", "logic_side"],
    ["l0", "board"], ["l1", "coffee"], ["board", "coffee"], ["l0", "chatA"], ["l1", "chatB"], ["chatA", "chatB"],
  ]);

  const spots = {
    coffee: { node: "coffee", yaw: Math.PI },
    board: { node: "board", yaw: Math.PI },
    chatA: { node: "chatA", yaw: Math.PI / 2 },
    chatB: { node: "chatB", yaw: -Math.PI / 2 },
    fax: { node: "fax", yaw: Math.PI },
    tray: { node: "tray", yaw: 0 },
  };

  return { root, desks, graph, spots, printer, printerOut, trayPos, trayGlow, monitors };
}
