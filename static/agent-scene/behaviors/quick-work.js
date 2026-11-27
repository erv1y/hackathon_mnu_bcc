// Быстрая проверка: агент отдыхает на диване, по уведомлению открывает MacBook.
import { pick, rand, smooth } from "../kit.js";
import { T } from "../actor.js";
import { setLaptopOpen } from "../props.js";

const IDLE_PHRASES = ["sceneHome1", "sceneHome2", "sceneHome3", "sceneHome4"];

export class QuickAgent {
  constructor(ctx, actor, gamepad) {
    this.ctx = ctx;
    this.actor = actor;
    this.home = ctx.apartment;
    this.gamepad = gamepad;
    this.laptopOpen = 0;
    this.laptopTarget = 0;
    this.phoneGlow = 0;
    this.phoneTarget = 0;
    this.working = false;
    this.next = rand(3, 6);
    actor.seatNow(this.home.seat);
    actor.base.game = 1;
    actor.character.hold.add(gamepad);
    gamepad.position.set(0, 0.02, 0);
  }

  holdPad(inHand) {
    if (inHand) {
      this.actor.character.hold.add(this.gamepad);
      this.gamepad.position.set(0, 0.02, 0);
      this.gamepad.rotation.set(0, 0, 0);
    } else {
      this.home.root.attach(this.gamepad);
      this.gamepad.position.copy(this.home.padRest);
      this.gamepad.rotation.set(0, this.home.seat.yaw, 0);
    }
  }

  holdCan(inHand) {
    const can = this.home.can;
    if (inHand) {
      this.actor.character.handR.add(can);
      can.position.set(0, -0.04, 0.02);
    } else {
      this.home.root.add(can);
      can.position.copy(this.home.canRest);
    }
  }

  idleBeat() {
    const a = this.actor;
    const roll = Math.random();
    if (roll < 0.4) {
      a.queue(
        T.call(() => { a.base.game = 0; this.holdPad(false); }),
        T.act("reach", 0.7, null, "sceneStatusDrink"),
        T.call(() => this.holdCan(true)),
        T.act("drink", rand(2.5, 3.5), null, "sceneStatusDrink"),
        T.act("reach", 0.6),
        T.call(() => { this.holdCan(false); this.holdPad(true); a.base.game = 1; a.status = "sceneStatusGaming"; }),
      );
    } else if (roll < 0.7) {
      a.queue(
        T.call(() => { a.base.lean = 1; }),
        T.wait(rand(3, 5), null, "sceneStatusGaming"),
        T.call(() => { a.base.lean = 0; }),
      );
    } else {
      a.queue(T.call(() => this.ctx.overlay.say(a, this.ctx.t(pick(IDLE_PHRASES)), 3)));
    }
  }

  start() {
    const a = this.actor;
    this.reset();
    this.working = true;
    this.resultSeen = false;
    this.fast = false;
    a.role = "quick";
    a.clear();
    a.base.lean = 0;
    a.base.active = 1;
    if (a.character.handR.children.includes(this.home.can)) this.holdCan(false);
    this.phoneTarget = 1;
    a.queue(
      T.wait(0.2, null, "sceneStatusNotice"),
      T.call(() => { this.ctx.overlay.say(a, this.ctx.t("scenePhoneNotice"), 2, "pipeline"); a.base.look = -0.6; }),
      T.wait(0.7),
      T.call(() => { a.base.game = 0; this.holdPad(false); a.base.look = 0; }),
      T.act("reach", 0.5),
      T.call(() => { this.laptopTarget = 1; this.phoneTarget = 0.3; }),
      T.act("type", 1.4, (_, task) => task.elapsed > (this.fast ? 0.3 : 1.4) && (this.resultSeen || this.fast), "sceneStatusTyping"),
      T.call(() => { this.laptopTarget = 0; this.phoneTarget = 0; }),
      T.wait(0.5),
      T.call(() => {
        this.ctx.overlay.say(a, this.ctx.t("sceneQuickDone"), 3, "done");
        this.ctx.setState("completed");
      }),
      T.act("cheer", 1.3, null, "sceneStatusDone"),
      T.call(() => {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.end(), 1800);
      }),
      T.wait(0, () => !this.working),
    );
  }

  result() { this.resultSeen = true; }

  finish() {
    if (!this.working) return;
    this.resultSeen = true;
    this.fast = true;
    this.actor.timeScale = 2;
  }

  end() {
    const a = this.actor;
    this.working = false;
    a.timeScale = 1;
    a.base.active = 0;
    a.clear();
    a.role = "idle";
    this.laptopTarget = 0;
    this.phoneTarget = 0;
    this.holdPad(true);
    a.base.game = 1;
    if (this.ctx.state === "completed" || this.ctx.state === "error") this.ctx.setState("idle");
  }

  error() {
    if (!this.working) return;
    this.ctx.overlay.say(this.actor, this.ctx.t("scenePipeError"), 2.6, "pipeline");
    clearTimeout(this.timer);
    this.end();
  }

  reset() {
    clearTimeout(this.timer);
    if (this.working) this.end();
  }

  update(dt, time) {
    const a = this.actor;
    if (a.role === "idle" && !a.busy) {
      this.next -= dt;
      a.status = "sceneStatusGaming";
      if (this.next <= 0) { this.idleBeat(); this.next = rand(5, 10); }
    }
    this.laptopOpen = smooth(this.laptopOpen, this.laptopTarget, dt, 5);
    setLaptopOpen(this.home.laptop, this.laptopOpen);
    this.phoneGlow = smooth(this.phoneGlow, this.phoneTarget, dt, 6);
    this.home.phone.userData.screen.emissiveIntensity = this.phoneGlow * (1.2 + Math.sin(time * 9) * 0.4);
    const gaming = (a.weights.game || 0);
    this.home.tvGlow.intensity = 0.25 + gaming * 0.5 + Math.sin(time * 6) * 0.06;
    this.home.game.draw(time, this.ctx.palette);
  }
}
