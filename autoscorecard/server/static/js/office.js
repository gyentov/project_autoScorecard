// The pixel office: map, pathfinding, the Boss avatar, the team's life simulation and
// the animations for REAL events from the backend (handoffs, emails, approvals, meetings).
// The life simulation (coffee, snacks, water) is cosmetic: it runs only in the browser and
// never waits on or delays real work.
import { TEAM, sleep } from "./api.js";

const T = 32;
const FW = 48, FH = 64;                       // sprite frame (see scripts/art/map_sprites.py)
const FR = { down: 0, left: 1, right: 2, up: 3 };     // standing frames; +4 = mid-stride
// real sitting frames (row 3 of the sheet): on a chair seen from behind, seiza on the floor
const SIT = { chair: 8, left: 9, right: 10, up: 11, down: 11 };
const hasSit = (id) => O.scene.textures.get(id).frameTotal > 12;
const SIT_DY = 4, SIT_CROP = 20;              // a seated person: shifted up, legs hidden
const FLOOR_DY = 20;                          // sitting on the floor: lower, legs under the futon
const HEAD = 70;                              // bubble height above the feet
const AGENTS = ["laras", "bening", "citra", "kirana"];
// where each habit is satisfied in the pantry
const DRINK_AT = { laras: "dispenser", bening: "dispenser", citra: "coffee", kirana: "vending" };
const NEED_RATE = { hunger: 5, thirst: 8, sleepy: 6 };        // points per minute
const SLEEPY_BIAS = { citra: 1.4, laras: 0.8, bening: 1, kirana: 0.9 };
// what floats over a desk now and then while its owner works
const WORK_ICON = { laras: "📋", bening: "🧹", citra: "📈", kirana: "🎨" };

export const O = {
  scene: null, layout: null, actors: {}, boss: null, bubbles: [], meeting: null,
  handlers: {},                // callbacks set by main.js: agentClick, mailbox, whiteboard, enterMeeting
  timeline: [],
};

const tileXY = (tx, ty) => ({ x: tx * T + 16, y: ty * T + 31 });
const pick = (xs) => xs[Math.floor(Math.random() * xs.length)];
const dirTo = (from, to) => {
  const dx = to.tx - from.tx, dy = to.ty - from.ty;
  return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up");
};

// --------------------------------------------------------------------------- pathfinding
function walkable(tx, ty) {
  const L = O.layout;
  return tx >= 0 && ty >= 0 && tx < L.cols && ty < L.rows && L.solid[ty][tx] === ".";
}

function nearestWalkable(tx, ty, from) {
  if (walkable(tx, ty)) return [tx, ty];
  const cand = [];
  for (let r = 1; r < 4 && !cand.length; r++) {
    for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) === r && walkable(tx + dx, ty + dy)) cand.push([tx + dx, ty + dy]);
    }
  }
  cand.sort((a, b) => (Math.abs(a[0] - from[0]) + Math.abs(a[1] - from[1])) -
                      (Math.abs(b[0] - from[0]) + Math.abs(b[1] - from[1])));
  return cand[0] || from;
}

function findPath(sx, sy, gx, gy) {
  [gx, gy] = nearestWalkable(gx, gy, [sx, sy]);
  const key = (x, y) => y * 100 + x;
  const prev = new Map([[key(sx, sy), null]]);
  const q = [[sx, sy]];
  while (q.length) {
    const [x, y] = q.shift();
    if (x === gx && y === gy) break;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (walkable(nx, ny) && !prev.has(key(nx, ny))) { prev.set(key(nx, ny), [x, y]); q.push([nx, ny]); }
    }
  }
  if (!prev.has(key(gx, gy))) return [];
  const path = [];
  for (let c = [gx, gy]; c && !(c[0] === sx && c[1] === sy); c = prev.get(key(c[0], c[1]))) path.unshift(c);
  return path;
}

// --------------------------------------------------------------------------- actors
class Actor {
  constructor(scene, id, tx, ty) {
    this.id = id; this.tx = tx; this.ty = ty; this.dir = "down"; this.token = 0; this.seated = false;
    const p = tileXY(tx, ty);
    this.sprite = scene.add.sprite(p.x, p.y, id, FR.down).setOrigin(0.5, 1);
    this.sprite.setDepth(p.y);
  }
  get pos() { return { x: this.sprite.x, y: this.sprite.y }; }
  place(tx, ty, dir = "down") {
    this.stand();
    const p = tileXY(tx, ty);
    this.tx = tx; this.ty = ty;
    this.sprite.setPosition(p.x, p.y).setDepth(p.y);
    this.face(dir);
  }
  face(dir) {
    this.stand();
    this.dir = dir;
    this.sprite.anims.stop();
    this.sprite.setFrame(FR[dir]);
  }
  // sit on the chair of the tile you stand on, back to the camera; the chair's backrest is
  // drawn in front, so the head, shoulders and arms show above it, as at a real desk
  sit() {
    if (this.seated) return;
    this.calm();
    this.sprite.anims.stop();
    this.dir = "up";
    const p = tileXY(this.tx, this.ty);
    if (hasSit(this.id)) {
      this.sprite.setFrame(SIT.chair).setPosition(p.x, p.y - 21).setDepth(p.y - 1);
    } else {                                        // sheets without sitting frames
      this.sprite.setFrame(FR.up).setPosition(p.x, p.y - SIT_DY)
        .setCrop(0, 0, FW, FH - SIT_CROP).setDepth(p.y - 1);
    }
    this.seated = true;
  }
  // kneel on the floor (seiza, at the kotatsu), facing `dir`
  sitFloor(dir) {
    this.stand();
    this.calm();
    this.sprite.anims.stop();
    this.dir = dir;
    const p = tileXY(this.tx, this.ty);
    if (hasSit(this.id)) {
      this.sprite.setFrame(SIT[dir]).setPosition(p.x, p.y - 3).setDepth(p.y);
    } else {
      this.sprite.setFrame(FR[dir]).setPosition(p.x, p.y + FLOOR_DY)
        .setCrop(0, 0, FW, FH - FLOOR_DY - 2).setDepth(p.y);
    }
    this.seated = true;
  }
  stand() {
    if (!this.seated) return;
    const p = tileXY(this.tx, this.ty);
    this.sprite.setPosition(p.x, p.y).setCrop().setDepth(p.y);
    this.seated = false;
  }
  calm() {                                         // stop any act tween and reset the pose
    if (this.tween) { this.tween.stop(); this.tween = null; }
    this.sprite.setScale(1).setAngle(0);
    if (!this.seated) this.sprite.setY(tileXY(this.tx, this.ty).y);   // e.g. after a cheer jump
  }
  step(nx, ny) {
    this.stand();
    this.calm();
    const dir = nx > this.tx ? "right" : nx < this.tx ? "left" : ny > this.ty ? "down" : "up";
    this.dir = dir;
    this.sprite.anims.play(`${this.id}_walk_${dir}`, true);
    const p = tileXY(nx, ny);
    this.tx = nx; this.ty = ny;
    return new Promise((res) => O.scene.tweens.add({
      targets: this.sprite, x: p.x, y: p.y, duration: this.id === "boss" ? 190 : 250,
      onUpdate: () => this.sprite.setDepth(this.sprite.y), onComplete: res }));
  }
  async walkTo(tx, ty, face) {
    const my = ++this.token;
    for (const [nx, ny] of findPath(this.tx, this.ty, tx, ty)) {
      if (my !== this.token) return false;            // a newer walk cancelled this one
      await this.step(nx, ny);
    }
    if (my !== this.token) return false;
    this.face(face || this.dir);
    return true;
  }
  // small acted moments: the sprites have no special frames, so these are tweens
  async act(kind, ms) {
    this.stand();
    this.face("down");
    const s = this.sprite;
    const y0 = s.y;
    if (kind === "cheer") {
      this.tween = O.scene.tweens.add({ targets: s, y: y0 - 10, duration: 180, yoyo: true, repeat: -1, ease: "Quad.easeOut" });
    } else if (kind === "bow") {
      this.tween = O.scene.tweens.add({ targets: s, scaleY: 0.88, duration: 300, yoyo: true, hold: 500 });
    } else if (kind === "yawn") {
      this.tween = O.scene.tweens.add({ targets: s, scaleY: 1.06, angle: -3, duration: 700, yoyo: true, repeat: -1 });
    } else {                                           // drink / eat / tablet: a gentle sway
      this.tween = O.scene.tweens.add({ targets: s, angle: 3, duration: 450, yoyo: true, repeat: -1 });
    }
    await sleep(ms);
    this.calm();
  }
  at(spot) { return this.tx === spot.x && this.ty === spot.y; }
}

class Agent extends Actor {
  constructor(scene, id, desk) {
    super(scene, id, desk.x, desk.y);
    this.desk = desk; this.status = "idle"; this.task = ""; this.errands = []; this.talking = false;
    this.needs = { hunger: Math.random() * 60, thirst: Math.random() * 70, sleepy: Math.random() * 50 };
    this.lastNotice = 0;
    this.lastIcon = Date.now() + Math.random() * 8000;
    this.restUntil = 0;
    this.sit();                                       // the day already started: at her desk
  }
  // ...or up and about somewhere else when the Boss walks in
  startAt(spot) {
    this.place(spot.x, spot.y, spot.face || "up");
    if (spot.floor) this.sitFloor(spot.face);
    this.restUntil = Date.now() + 4000 + Math.random() * 8000;
  }
  urgent() {
    const [k, v] = Object.entries(this.needs).sort((a, b) => b[1] - a[1])[0];
    return v >= 80 ? k : null;
  }
  get working() { return this.seated && this.at(this.desk) && this.status === "working"; }
  async loop() {
    for (;;) {
      try { await this.tick(); } catch (e) { console.error(this.id, e); }
      await sleep(250);
    }
  }
  async tick() {
    if (this.scripted) return;                    // a scripted walk (arrival, briefing) is running
    if (this.talking) { this.face(this.boss_dir || "down"); return; }
    const m = O.meeting;
    if (m && m.attendees.includes(this.id)) {                     // meetings first
      const spot = m.host === this.id ? O.layout.meeting.host
        : O.layout.meeting.seats[m.attendees.filter((a) => a !== m.host).indexOf(this.id)];
      if (!this.at(spot)) await this.walkTo(spot.x, spot.y, spot.face);
      else this.face(spot.face);
      return;
    }
    if (this.errands.length) { await this.errands.shift()(); return; }
    if (Date.now() < this.restUntil) { await sleep(400); return; }
    const need = this.urgent();
    if (need && this.status !== "waiting_boss" && (this.status !== "working" || this.needs[need] > 95)) {
      await this.satisfy(need);
      return;
    }
    if (!this.at(this.desk)) { await this.walkTo(this.desk.x, this.desk.y, "up"); return; }
    if (this.status === "waiting_boss") {         // keep working, and remind the Boss now and then
      this.sit();
      if (Date.now() - this.lastNotice > 9000) {
        this.lastNotice = Date.now();
        O.emote(this, "❗ menunggu Boss", 2500);
      }
      await sleep(600);
    } else if (this.status === "working") {
      this.sit();
      if (Date.now() - this.lastIcon > 14000) { this.lastIcon = Date.now(); O.float(this, WORK_ICON[this.id]); }
      await sleep(800);
    } else if (Math.random() < 0.015) {                          // idle: stretch your legs
      await this.wander();
    } else {
      this.sit();
      await sleep(900);
    }
  }
  async goSit(s) {                                 // walk to a spot; at the kotatsu, sit down
    const ok = await this.walkTo(s.x, s.y, s.face);
    if (ok && s.floor) this.sitFloor(s.face);
    return ok;
  }
  async wander() {
    const L = O.layout;
    const spots = [L.whiteboard, ...L.lounge, ...L.pantry.table, ...L.huddle];
    const s = pick(spots);
    await this.goSit(s);
    if (s === L.whiteboard) O.emote(this, "🤔", 2000);
    await sleep(2500 + Math.random() * 3000);
  }
  async satisfy(need) {
    const P = O.layout.pantry;
    const t = TEAM[this.id];
    if (need === "thirst") {
      const st = P[DRINK_AT[this.id]];
      await this.walkTo(st.x, st.y, "up");
      O.emote(this, DRINK_AT[this.id] === "coffee" ? "☕" : "🥤", 1500);
      await sleep(1500);
      await this.walkTo(this.desk.x, this.desk.y, "up");
      this.sit();
      O.emote(this, `${t.drink} 😌`, 2200);
      await sleep(2600);
    } else if (need === "sleepy") {
      O.emote(this, "😪", 1200);
      await this.act("yawn", 1400);
      if (Math.random() < 0.5) {                     // a short rest on the tatami
        const seat = pick(O.layout.lounge);
        await this.goSit(seat);
        O.emote(this, "💤 istirahat di kotatsu", 3000);
        await sleep(3500);
      } else {
        await this.walkTo(P.coffee.x, P.coffee.y, "up");
        O.emote(this, "☕ ngopi dulu", 1800);
        await sleep(1800);
        const seat = pick(P.table);
        await this.walkTo(seat.x, seat.y, seat.face);
        await this.act("drink", 3000);
      }
    } else {
      await this.walkTo(P.vending.x, P.vending.y, "up");
      O.emote(this, "🪙", 1200);
      await sleep(1300);
      if (t.snack === "frozen meal") {                           // heat it first
        await this.walkTo(P.microwave.x, P.microwave.y, "up");
        O.emote(this, "⏳ microwave…", 3000);
        await sleep(3200);
        O.emote(this, "🔔 ting!", 1200);
        await sleep(800);
      }
      const seat = pick([...P.table, ...O.layout.lounge]);
      await this.goSit(seat);
      O.emote(this, `${t.snack} 😋`, 2000);
      if (seat.floor) await sleep(3500);          // eating at the kotatsu: stay seated
      else await this.act("eat", 3500);
    }
    this.needs[need] = Math.random() * 10;
  }
}

// --------------------------------------------------------------------------- keyboard
// The last direction pressed wins, so rolling from A to W turns at once even while A is
// still held; everything is released when the window loses focus.
const KEY_DIR = { KeyW: "up", ArrowUp: "up", KeyS: "down", ArrowDown: "down", KeyA: "left",
  ArrowLeft: "left", KeyD: "right", ArrowRight: "right" };
const held = [];
const typingNow = () => ["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName);

function setupKeys() {
  window.addEventListener("keydown", (e) => {
    if (typingNow() || e.ctrlKey || e.metaKey || e.altKey) return;
    const dir = KEY_DIR[e.code];
    if (dir) {
      e.preventDefault();
      const i = held.indexOf(dir);
      if (i >= 0) held.splice(i, 1);
      held.push(dir);
    } else if (e.code === "KeyE" && !e.repeat) interact();
  });
  window.addEventListener("keyup", (e) => {
    const dir = KEY_DIR[e.code];
    const i = dir ? held.indexOf(dir) : -1;
    if (i >= 0) held.splice(i, 1);
  });
  const release = () => { held.length = 0; };
  window.addEventListener("blur", release);
  document.addEventListener("visibilitychange", release);
}

// --------------------------------------------------------------------------- the scene
class OfficeScene extends Phaser.Scene {
  constructor() { super("office"); }
  preload() {
    this.load.image("office", "/assets/office/office.png");
    this.load.image("chair_back", "/assets/office/chair_back.png");
    for (const f of O.layout.fronts || []) this.load.image(`front_${f.key}`, `/assets/office/front_${f.key}.png`);
    for (const id of [...AGENTS, "boss"]) {
      this.load.spritesheet(id, `/assets/sprites/${id}.png`, { frameWidth: FW, frameHeight: FH });
    }
  }
  create() {
    O.scene = this;
    this.add.image(0, 0, "office").setOrigin(0, 0).setDepth(-1000);
    for (const id of [...AGENTS, "boss"]) {
      for (const [dir, f] of Object.entries(FR)) {
        this.anims.create({ key: `${id}_walk_${dir}`, repeat: -1, frameRate: 6,
          frames: this.anims.generateFrameNumbers(id, { frames: [f + 4, f] }) });
      }
    }
    const L = O.layout;
    for (const c of L.chairs) {     // backrests: in front of whoever SITS, behind anyone standing
      this.add.image(c.x - L.chair_size[0] / 2, c.y, "chair_back").setOrigin(0, 0).setDepth(c.feet - 0.5);
    }
    for (const f of L.fronts || []) {              // pieces people pass behind or under
      this.add.image(f.x, f.y, `front_${f.key}`).setOrigin(0, 0).setDepth(f.depth);
    }
    this.fx = this.add.graphics().setDepth(-900);   // desk work animations
    this.gate = this.add.graphics().setDepth(-850);  // the automatic door's glass leaves
    this.gateOpen = 0;
    for (const id of AGENTS) O.actors[id] = new Agent(this, id, L.desks[id]);
    // one or two of the team are away from their desks when the Boss arrives
    const away = [L.whiteboard, ...L.lounge, ...L.pantry.table, L.pantry.coffee, L.pantry.dispenser, ...L.huddle];
    for (const id of AGENTS.filter((a) => a !== "laras" && Math.random() < 0.45).slice(0, 2)) {
      O.actors[id].startAt(away.splice(Math.floor(Math.random() * away.length), 1)[0]);
    }
    O.boss = new Actor(this, "boss", L.entrance.x, L.entrance.y);
    O.boss.face("up");
    this.overlay = this.add.graphics().setDepth(-899);
    this.doorGlow = this.add.graphics().setDepth(-898);
    this.tweens.add({ targets: this.doorGlow, alpha: { from: 0.25, to: 0.9 }, duration: 700, yoyo: true, repeat: -1 });
    setupKeys();
    setupMarkers(this);
    this.input.on("pointerdown", (p) => onClick(p.worldX, p.worldY));
    this.input.on("pointermove", (p) => onHover(p));
    this.input.on("gameout", () => onHover(null));
    this.time.addEvent({ delay: 1000, loop: true, callback: tickNeeds });
    this.time.addEvent({ delay: 120, loop: true, callback: drawDeskFx });
    drawOverlay();
    for (const a of Object.values(O.actors)) a.loop();
    O.ready?.();
  }
  update() {
    const b = O.boss;
    if (!typingNow() && !b.stepping && !b.scripted) {
      const dir = held[held.length - 1];
      if (dir) {
        b.token++;                                                // cancel click-walking
        const [dx, dy] = { left: [-1, 0], right: [1, 0], up: [0, -1], down: [0, 1] }[dir];
        if (walkable(b.tx + dx, b.ty + dy)) {
          b.stepping = true;
          b.step(b.tx + dx, b.ty + dy).then(() => { b.stepping = false; b.kbd = true; checkBossPlace(); });
        } else if (b.dir !== dir || b.seated) {
          b.face(dir);
        }
      } else if (b.kbd) {                                         // keys released: stand still
        b.kbd = false;
        b.face(b.dir);
      }
    }
    for (const bb of O.bubbles) placeBubble(bb);
    placeMarkers();
    drawGate(this);
  }
}

// the automatic door between the office front and the office: it slides open when anyone
// is within a tile of the doorway and closes behind them
function drawGate(scene) {
  const G = O.layout.gate;
  if (!G) return;
  const near = [O.boss, ...Object.values(O.actors)].some((a) =>
    Math.abs(a.tx - G.x) <= 1 && a.ty >= G.rows[0] - 1 && a.ty <= G.rows[G.rows.length - 1] + 1);
  scene.gateOpen += ((near ? 1 : 0) - scene.gateOpen) * 0.18;
  const half = (G.y1 - G.y0) / 2;
  const slide = scene.gateOpen * (half - 4);
  const g = scene.gate;
  g.clear();
  for (const [top, dir] of [[G.y0, -1], [G.y0 + half, 1]]) {
    const y = top + dir * slide;
    g.fillStyle(0xb4d8e8, 0.95).fillRect(G.px, y, 8, half);
    g.fillStyle(0xe2f2f8).fillRect(G.px + 1, y, 1, half);
    g.lineStyle(1, 0x707a86).strokeRect(G.px, y, 8, half);
  }
}

function tickNeeds() {
  for (const a of Object.values(O.actors)) {
    if (O.meeting && O.meeting.attendees.includes(a.id)) continue;
    a.needs.hunger += NEED_RATE.hunger / 60;
    a.needs.thirst += NEED_RATE.thirst / 60;
    a.needs.sleepy += (NEED_RATE.sleepy * SLEEPY_BIAS[a.id]) / 60;
  }
}

// --------------------------------------------------------------------------- desk work
// While someone is working at her desk, her screen and her job props move: Bening moves
// sheets from the RAW tray to the OK tray, Citra's score curve draws itself, Kirana
// sketches and flips slides, Laras's plan fills up and her phone blinks.
function drawDeskFx() {
  const g = O.scene.fx;
  g.clear();
  const t = Date.now() / 1000;
  for (const a of Object.values(O.actors)) {
    if (!a.working) continue;
    const fx = O.layout.desk_fx[a.id];
    const [sx, sy, sw, sh] = fx.screen;
    const [px, py] = fx.prop;
    g.fillStyle(0x202840).fillRect(sx, sy, sw, sh);
    if (a.id === "bening") {
      const off = Math.floor(t * 6) % 4;
      for (let k = 0; k < 5; k++) {
        const y = sy + 2 + ((k * 4 + off) % (sh - 3));
        g.fillStyle(0x5a6e96).fillRect(sx + 2, y, sw - 4, 1);
        g.fillStyle(k === 2 ? 0x78dc8c : 0x8296c8).fillRect(sx + 2, y, 6, 1);
      }
      const ph = (t % 1.6) / 1.6;                                  // a sheet: RAW -> OK
      const x = px + 1 + ph * 11, y = py + 4 - Math.sin(ph * Math.PI) * 6;
      g.fillStyle(0xf6f4ec).fillRect(x, y, 7, 3);
      g.lineStyle(1, 0xaaaab0).strokeRect(x, y, 7, 3);
    } else if (a.id === "citra") {
      const n = Math.floor((t * 4) % 24);
      g.lineStyle(1, 0x5a6482).lineBetween(sx + 2, sy + sh - 2, sx + sw - 2, sy + sh - 2);
      g.lineStyle(1, 0xfa8caa).beginPath().moveTo(sx + 2, sy + sh - 2);
      for (let k = 1; k <= n; k++) {
        const u = k / 24;
        g.lineTo(sx + 2 + u * (sw - 4), sy + sh - 2 - Math.sqrt(u) * (sh - 5));
      }
      g.strokePath();
      g.fillStyle(Math.floor(t * 3) % 2 ? 0xaadcaa : 0x8cc88c).fillRect(px + 1, py + 3, 7, 2);
    } else if (a.id === "kirana") {
      const slide = Math.floor(t / 1.5) % 3;
      g.fillStyle(0xf0ece4).fillRect(sx + 2, sy + 2, sw - 4, sh - 4);
      g.fillStyle([0xe8a33d, 0xff78be, 0x5adce6][slide]).fillRect(sx + 4, sy + 4, 10, 3);
      g.fillStyle(0x9696a0).fillRect(sx + 4, sy + 9, ((t * 8) % (sw - 8)), 1);
      g.fillStyle(0x9696a0).fillRect(sx + 4, sy + 12, sw - 12, 1);
      g.lineStyle(1, 0xffffff);                                    // stylus sketch on the tablet
      const k = (t * 3) % 1;
      g.lineBetween(px + 1, py + 6 - k * 3, px + 1 + k * 11, py + 3 + Math.sin(t * 9) * 2);
    } else if (a.id === "laras") {
      for (let k = 0; k < 3; k++) {
        const w = Math.min(sw - 6, ((t * 5 + k * 7) % (sw - 2)));
        g.fillStyle(k === 1 ? 0xe8a33d : 0x78b4fa).fillRect(sx + 3 + k * 4, sy + 3 + k * 5, Math.max(2, w - k * 4), 2);
      }
      g.fillStyle(Math.floor(t * 2) % 2 ? 0x78e68c : 0x30303a).fillRect(px, py, 3, 2);
    }
  }
}

// --------------------------------------------------------------------------- what can be clicked
// Objects the Boss can use carry a small bobbing tag; hovering anything clickable shows
// a label and a hand cursor.
function hotspots() {
  const L = O.layout;
  const [mx, my] = L.mailbox.tile;
  const [dx, dy, dw, dh] = L.drawer.tile;
  const d = L.meeting.door;
  const spots = [
    { rect: [mx, my - 1, 1, 2], icon: "✉", label: "Kotak surat · inbox", at: [mx * T + 16, my * T - 24] },
    { rect: L.whiteboard.tile, icon: "📊", label: "Whiteboard · timeline",
      at: [(L.whiteboard.tile[0] + L.whiteboard.tile[2] / 2) * T, 8] },
    { rect: [dx, dy, dw, dh], icon: "🗄", label: "Meja Boss · duduk, laci = log percakapan",
      at: [(dx + dw) * T - 17, dy * T + 46] },                        // on the drawer pedestal
  ];
  if (O.meeting?.open) {
    spots.push({ rect: [d.x, d.y, 1, 1], icon: "🚪", label: "Ruang rapat · rapat siap, masuk",
      at: [d.x * T + 16, d.y * T - 6] });
  }
  return spots;
}

function setupMarkers(scene) {
  scene.markers = [];
  refreshMarkers();
}

// page elements (not canvas text) so every emoji renders like the rest of the UI
export function refreshMarkers() {
  const scene = O.scene;
  if (!scene) return;
  for (const m of scene.markers) m.el.remove();
  scene.markers = hotspots().map((h) => {
    const el = document.createElement("div");
    el.className = "marker";
    el.textContent = h.icon;
    document.getElementById("bubbles").appendChild(el);
    return { el, at: h.at };
  });
  placeMarkers();
}

function placeMarkers() {
  const canvas = O.scene.game.canvas;
  const r = canvas.getBoundingClientRect();
  const stage = document.getElementById("stage").getBoundingClientRect();
  const s = r.width / canvas.width;
  for (const m of O.scene.markers) {
    m.el.style.left = `${r.left - stage.left + m.at[0] * s}px`;
    m.el.style.top = `${r.top - stage.top + m.at[1] * s}px`;
  }
}

function onHover(p) {
  const tip = document.getElementById("hover-tip");
  const canvas = O.scene.game.canvas;
  let label = null;
  if (p) {
    const a = Object.values(O.actors).find((x) => x.sprite.getBounds().contains(p.worldX, p.worldY));
    if (a) label = `💬 Ngobrol dengan ${TEAM[a.id].name}`;
    else {
      const tx = Math.floor(p.worldX / T), ty = Math.floor(p.worldY / T);
      const h = hotspots().find(({ rect: [x, y, w, hh] }) => tx >= x && tx < x + w && ty >= y && ty < y + hh);
      if (h) label = `${h.icon} ${h.label}`;
    }
  }
  canvas.style.cursor = label ? "pointer" : "default";
  tip.classList.toggle("hidden", !label);
  if (!label) return;
  tip.textContent = label;
  const r = canvas.getBoundingClientRect();
  const stage = document.getElementById("stage").getBoundingClientRect();
  tip.style.left = `${r.left - stage.left + p.x * (r.width / canvas.width)}px`;
  tip.style.top = `${r.top - stage.top + p.y * (r.height / canvas.height)}px`;
}

// page (viewport) coordinates of a point on the map: the tutorial points its arrow there
export function screenOf(mx, my) {
  const canvas = O.scene.game.canvas;
  const r = canvas.getBoundingClientRect();
  return { x: r.left + mx * (r.width / canvas.width), y: r.top + my * (r.height / canvas.height) };
}

// --------------------------------------------------------------------------- clicks
function onClick(wx, wy) {
  if (O.boss.scripted) return;
  for (const a of Object.values(O.actors)) {
    if (a.sprite.getBounds().contains(wx, wy)) { O.handlers.agentClick?.(a.id); return; }
  }
  const tx = Math.floor(wx / T), ty = Math.floor(wy / T);
  const L = O.layout;
  const [mx, my] = L.mailbox.tile;
  if (tx === mx && (ty === my || ty === my - 1)) { goThen(L.mailbox, () => O.handlers.mailbox?.()); return; }
  const [dx, dy, dw, dh] = L.drawer.tile;                 // the Boss desk drawer: the log
  if (tx >= dx && tx < dx + dw && ty >= dy && ty < dy + dh) {
    goThen(L.boss_desk, () => { O.boss.sit(); O.handlers.drawer?.(); });
    return;
  }
  const [wbx, wby, wbw, wbh] = L.whiteboard.tile;
  if (tx >= wbx && tx < wbx + wbw && ty >= wby && ty < wby + wbh) {
    goThen(L.whiteboard, () => O.handlers.whiteboard?.());
    return;
  }
  const bd = L.boss_desk;
  O.boss.walkTo(tx, ty).then((done) => {
    if (!done) return;
    if (O.boss.at(bd)) O.boss.sit();                // back at your own desk: sit down
    checkBossPlace();
  });
}

async function goThen(spot, fn) {
  if (await O.boss.walkTo(spot.x, spot.y, spot.face)) fn();
}

function interact() {                     // E: talk to the nearest teammate, or use what's in front
  if (O.boss.scripted) return;
  const near = Object.values(O.actors).find((a) => Math.abs(a.tx - O.boss.tx) + Math.abs(a.ty - O.boss.ty) <= 1);
  if (near) { O.handlers.agentClick?.(near.id); return; }
  const L = O.layout;
  if (O.boss.at(L.mailbox)) O.handlers.mailbox?.();
  else if (O.boss.at(L.whiteboard)) O.handlers.whiteboard?.();
  else if (O.boss.at(L.boss_desk)) O.boss.sit();
}

function checkBossPlace() {
  const [x0, y0, w, h] = O.layout.meeting.area;
  const inside = O.boss.tx >= x0 && O.boss.tx < x0 + w && O.boss.ty >= y0 && O.boss.ty < y0 + h;
  if (inside && O.meeting?.open) O.handlers.enterMeeting?.();
}

// --------------------------------------------------------------------------- overlays
const STATUS_COLOR = { done: 0x2e7d4f, in_progress: 0x2a78d6, waiting_boss: 0xe08a1e, todo: 0xb4b4be };

export function drawOverlay() {
  const g = O.scene?.overlay;
  if (!g) return;
  g.clear();
  const items = O.timeline || [];                    // whiteboard: one dot per milestone
  const [x0, x1, y] = O.layout.whiteboard.dots;
  g.lineStyle(2, 0xc0c0c8).lineBetween(x0, y, x1, y);
  items.forEach((m, k) => {
    const x = items.length > 1 ? x0 + (k * (x1 - x0)) / (items.length - 1) : (x0 + x1) / 2;
    g.fillStyle(STATUS_COLOR[m.status] || 0xb4b4be).fillCircle(x, y, 5);
    g.lineStyle(1, 0x333340).strokeCircle(x, y, 5);
  });
  const dg = O.scene.doorGlow;
  dg.clear();
  if (O.meeting?.open) {
    const d = O.layout.meeting.door;
    dg.fillStyle(0xffd34d).fillRect(d.x * T + 8, d.y * T, 16, T);
  }
}

export function setTimeline(items) { O.timeline = items; drawOverlay(); }

export function setMeeting(m) {          // {id, host, attendees, open} or null
  const changed = !!O.meeting?.open !== !!m?.open;
  O.meeting = m;
  drawOverlay();
  if (changed) refreshMarkers();
}

// --------------------------------------------------------------------------- bubbles
function placeBubble(b) {
  const canvas = O.scene.game.canvas;
  const r = canvas.getBoundingClientRect();
  const stage = document.getElementById("stage").getBoundingClientRect();
  const s = r.width / canvas.width;
  const { x, y } = b.actor.pos;
  const top = b.actor.seated ? HEAD - SIT_DY : HEAD;
  b.el.style.left = `${r.left - stage.left + x * s}px`;
  b.el.style.top = `${r.top - stage.top + (y - top - (b.lift || 0)) * s}px`;
}

O.emote = function (actor, text, ms = 2500, cls = "bubble") {
  if (!actor) return;
  for (const b of O.bubbles.filter((b) => b.actor === actor && b.el.className === cls)) b.kill();
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = text;
  document.getElementById("bubbles").appendChild(el);
  const b = { el, actor, kill: () => { el.remove(); O.bubbles = O.bubbles.filter((x) => x !== b); } };
  O.bubbles.push(b);
  placeBubble(b);
  setTimeout(b.kill, ms);
  return b;
};

O.say = (actor, text, ms = 6000) => O.emote(actor, text, ms, "bubble say");
O.float = (actor, text) => O.emote(actor, text, 2400, "floater");

// --------------------------------------------------------------------------- real events
function envelope(fromActor) {
  const s = O.scene;
  const g = s.add.graphics().setDepth(5000);
  g.fillStyle(0xffffff).fillRect(-6, -4, 12, 8).lineStyle(1, 0x553333).strokeRect(-6, -4, 12, 8);
  g.lineBetween(-6, -4, 0, 1).lineBetween(6, -4, 0, 1);
  g.x = fromActor.pos.x; g.y = fromActor.pos.y - 50;
  const [mx, my] = O.layout.mailbox.tile;
  s.tweens.add({ targets: g, x: mx * T + 16, y: my * T - 4, duration: 1100, ease: "Sine.easeInOut",
    onComplete: () => { g.destroy(); bumpMailbox(); } });
}

function bumpMailbox() {
  const s = O.scene;
  const [mx, my] = O.layout.mailbox.tile;
  const g = s.add.graphics().setDepth(5000);
  g.fillStyle(0xffe066, 0.9).fillCircle(mx * T + 16, my * T - 10, 6);
  s.tweens.add({ targets: g, alpha: 0, y: -12, duration: 900, onComplete: () => g.destroy() });
}

const below = (spot) => ({ x: spot.x, y: spot.y + 1 });

export function onEvent(ev) {
  const a = O.actors[ev.actor];
  const d = ev.data || {};
  switch (ev.type) {
    case "agent_status":
      if (a) { a.status = d.status; a.task = d.task || ""; if (d.task) O.emote(a, d.task, 2500); }
      break;
    case "handoff": {
      const to = O.actors[d.to];
      if (a && to) a.errands.push(async () => {
        await a.walkTo(below(to.desk).x, below(to.desk).y, "up");
        O.emote(a, "📄 memo", 2000);
        O.emote(to, "👍", 2000);
        await sleep(2200);
      });
      break;
    }
    case "email_sent":
      if (a) { O.emote(a, "✉️", 1600); envelope(a); }
      break;
    case "proposal":
      if (a) a.errands.push(async () => {
        const e = O.layout.escort;
        await a.walkTo(e.x, e.y, e.face);
        O.emote(a, "📝 usulan untuk Boss", 2500);
        await sleep(2600);
      });
      break;
    case "board":
      if (a && !d.meeting_id) O.emote(a, "🖥 di papan", 2200);
      break;
    case "data_received":
      if (O.actors.laras) O.emote(O.actors.laras, "📁 data diterima!", 2600);
      break;
    case "approval":
      O.handlers.stamp?.(d.decision !== "rejected");
      if (O.actors.laras) O.emote(O.actors.laras, d.decision === "rejected" ? "🙇" : "✅", 2200);
      break;
    case "meeting_scheduled":
      if (a) a.errands.push(async () => {
        const w = O.layout.whiteboard;
        await a.walkTo(w.x, w.y, "up");
        O.emote(a, "📅 jadwal rapat", 2200);
        await sleep(2200);
      });
      break;
    case "job_started":
      if (a) { O.emote(a, "⚙️ build mulai", 2500); a.status = "working"; }
      break;
    case "job_progress":
      if (a && d.detail && d.detail.n_out !== undefined) O.float(a, `${d.step}: ${d.detail.n_in}→${d.detail.n_out}`);
      break;
    case "job_finished":
      if (a) {
        O.emote(a, d.ok ? "🎉 build selesai" : "❌ build gagal", 3500);
        if (d.ok) a.errands.push(() => a.act("cheer", 1600));
      }
      break;
    case "cost_estimate":
      if (O.actors.laras) O.emote(O.actors.laras, `🧮 ~$${(+d.high_usd || 0).toFixed(4)}`, 3000);
      break;
    case "cost_actual":
      if (O.actors.laras) O.float(O.actors.laras, `$${(+d.usd || 0).toFixed(4)}`);
      break;
    case "dialogue":
      if (a && !d.meeting_id && !O.handlers.isChattingWith?.(ev.actor)) O.say(a, d.text);
      break;
    default:
      break;
  }
}

// --------------------------------------------------------------------------- scripted moments
export function setTalking(id, on) {
  const a = O.actors[id];
  if (!a) return;
  a.talking = on;
  if (on) {
    a.token++;                                       // stop where she is and face the Boss
    a.boss_dir = dirTo(a, O.boss);
    a.face(a.boss_dir);
  }
}

export async function bossApproach(id) {
  const a = O.actors[id];
  a.token++;
  setTalking(id, true);
  const options = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dy]) => [a.tx + dx, a.ty + dy])
    .filter(([x, y]) => walkable(x, y));
  options.sort((p, q) => (Math.abs(p[0] - O.boss.tx) + Math.abs(p[1] - O.boss.ty)) -
                         (Math.abs(q[0] - O.boss.tx) + Math.abs(q[1] - O.boss.ty)));
  if (options.length && Math.abs(a.tx - O.boss.tx) + Math.abs(a.ty - O.boss.ty) > 1) {
    await O.boss.walkTo(options[0][0], options[0][1]);
  }
  O.boss.face(dirTo(O.boss, a));
  setTalking(id, true);
}

// The Boss arrives at the office front: Laras is waiting by reception, greets the Boss
// and walks with them to the Boss desk, right next to hers. Resolves when both are in place.
export async function arrival() {
  const L = O.layout;
  const a = O.actors.laras;
  const b = O.boss;
  a.scripted = true; b.scripted = true;
  a.token++;
  a.place(L.greeter.x, L.greeter.y, L.greeter.face);
  b.place(L.entrance.x, L.entrance.y, "up");
  await sleep(700);
  await b.walkTo(L.entrance.x, L.entrance.y - 1, "up");
  a.face(dirTo(a, b));
  O.say(a, "Selamat pagi, Boss! Mari, saya antar ke meja Boss.", 3200);
  await a.act("bow", 1100);
  await sleep(900);
  const e = L.escort, bd = L.boss_desk;
  const lead = a.walkTo(e.x, e.y, e.face);
  await sleep(550);                                 // the Boss follows a step behind
  await b.walkTo(bd.x, bd.y, "up");
  await lead;
  b.sit();
  b.scripted = false;
  a.scripted = false;
  setTalking("laras", true);
}

// later briefings (another project opened): Laras comes to wherever the Boss is
export async function larasWalksToBoss() {
  const a = O.actors.laras;
  a.scripted = true;
  a.talking = false;
  await sleep(300);                               // let a running tick finish
  const b = O.boss;
  const e = O.layout.escort;
  if (b.at(O.layout.boss_desk)) await a.walkTo(e.x, e.y, e.face);
  else await a.walkTo(b.tx, b.ty - 1, "down");
  setTalking("laras", true);
  a.scripted = false;
}

export function bossWalkToMeeting() {
  const d = O.layout.meeting.door;
  return O.boss.walkTo(d.x - 1, d.y, "left").then((ok) => { if (ok) checkBossPlace(); });
}

export function startOffice(layout, ready) {
  O.layout = layout;
  O.ready = ready;
  return new Phaser.Game({
    type: Phaser.AUTO, parent: "game", width: layout.cols * T, height: layout.rows * T,
    pixelArt: true, backgroundColor: "#2b2420", scene: OfficeScene,
    scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
  });
}
