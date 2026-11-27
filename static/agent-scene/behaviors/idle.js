// Живой офис в простое: работа за столом, кофе, smart board, разговоры парами.
import { T } from "../actor.js";
import { pick, rand } from "../kit.js";

const PHRASES = {
  work: ["sceneWork1", "sceneWork2", "sceneWork3", "sceneWork4"],
  coffee: ["sceneCoffee1", "sceneCoffee2", "sceneCoffee3"],
  board: ["sceneBoard1", "sceneBoard2", "sceneBoard3"],
  chat: ["sceneChat1", "sceneChat2", "sceneChat3", "sceneChat4", "sceneChat5", "sceneChat6"],
};

export class OfficeIdle {
  constructor(ctx) {
    this.ctx = ctx;
    this.reserved = new Map();
    this.chatter = 0;
  }

  free(spot) { return !this.reserved.has(spot); }

  reserve(actor, ...spots) {
    spots.forEach((spot) => this.reserved.set(spot, actor));
    actor.reservation = spots;
  }

  release(actor) {
    (actor.reservation || []).forEach((spot) => { if (this.reserved.get(spot) === actor) this.reserved.delete(spot); });
    actor.reservation = null;
  }

  idleActors() {
    return this.ctx.deepActors.filter((actor) => actor.role === "idle");
  }

  maybeSay(actor, list, chance = 0.5) {
    if (Math.random() < chance) this.ctx.overlay.say(actor, this.ctx.t(pick(list)), rand(2.6, 3.6));
  }

  work(actor, duration = rand(5, 11)) {
    const desk = this.ctx.office.desks[actor.config.desk];
    actor.queue(
      T.sit(desk.seat, "sceneStatusWorking"),
      T.act("type", duration * 0.45, null, "sceneStatusWorking"),
      T.call(() => this.maybeSay(actor, PHRASES.work, 0.25)),
      T.act("type", duration * 0.55),
    );
  }

  coffee(actor) {
    const spot = this.ctx.office.spots.coffee;
    this.reserve(actor, "coffee");
    actor.queue(
      T.goto(spot.node, "sceneStatusCoffee"),
      T.face(spot.yaw),
      T.act("reach", 0.6),
      T.call(() => { actor.mug.visible = true; }),
      T.face(spot.yaw - 2.4),
      T.call(() => this.maybeSay(actor, PHRASES.coffee, 0.6)),
      T.act("drink", rand(3.5, 5.5), null, "sceneStatusCoffee"),
      T.call(() => { actor.mug.visible = false; this.release(actor); }),
    );
    this.work(actor);
  }

  board(actor) {
    const spot = this.ctx.office.spots.board;
    this.reserve(actor, "board");
    actor.queue(
      T.goto(spot.node, "sceneStatusBoard"),
      T.face(spot.yaw),
      T.call(() => this.maybeSay(actor, PHRASES.board, 0.7)),
      T.act("gesture", rand(4, 6), null, "sceneStatusBoard"),
      T.call(() => this.release(actor)),
    );
    this.work(actor);
  }

  chat(actor, partner) {
    const { chatA, chatB } = this.ctx.office.spots;
    this.reserve(actor, "chatA", "chatB");
    partner.reservation = [];
    partner.clear();
    const length = rand(6, 9);
    let turns = 0;
    const talk = (who, other) => [
      T.goto(who === actor ? chatA.node : chatB.node, "sceneStatusChat"),
      T.face(who === actor ? chatA.yaw : chatB.yaw),
      T.wait(0, (_, task) => task.elapsed > 8 || ["wait", "act"].includes(other.current?.kind) || !other.busy),
      T.call(() => {
        if (turns++ === 0) this.ctx.overlay.say(who, this.ctx.t(pick(PHRASES.chat)), 3);
      }),
      T.act("talk", length * 0.5, null, "sceneStatusChat"),
      T.call(() => { if (who === partner) this.ctx.overlay.say(who, this.ctx.t(pick(PHRASES.chat)), 3); }),
      T.act("talk", length * 0.5),
    ];
    actor.queue(talk(actor, partner), T.call(() => this.release(actor)));
    partner.queue(talk(partner, actor));
    this.work(actor);
    this.work(partner);
  }

  plan(actor) {
    const roll = Math.random();
    const idle = this.idleActors().filter((other) => other !== actor && (!other.busy || other.current?.name === "type"));
    if (roll < 0.22 && this.free("chatA") && idle.length) return this.chat(actor, pick(idle));
    if (roll < 0.42 && this.free("coffee")) return this.coffee(actor);
    if (roll < 0.58 && this.free("board")) return this.board(actor);
    return this.work(actor);
  }

  // Начальная расстановка, чтобы офис выглядел живым с первого кадра.
  seed() {
    const [payments, kyc, security, logic] = this.ctx.deepActors;
    [payments, security].forEach((actor) => actor.seatNow(this.ctx.office.desks[actor.config.desk].seat));
    this.work(payments, rand(3, 8));
    this.work(security, rand(6, 10));
    const a = this.ctx.office.graph.point("l0");
    kyc.placeAt(a.x - 0.4, a.z, Math.PI / 2);
    logic.placeAt(a.x + 0.3, a.z + 0.8, 0);
    this.chat(kyc, logic);
  }

  update() {
    for (const actor of this.idleActors()) if (!actor.busy) this.plan(actor);
  }
}
