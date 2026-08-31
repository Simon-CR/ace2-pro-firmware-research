/* Acceptance test for the ACE panel mockup.
   "Can he do each of these, for any lane, without typing anything?"
   Runs the mockup's own predicate/control logic under a DOM stub. */
const fs = require("fs");
const page = fs.readFileSync(__dirname + "/index.html", "utf8");

const el = () => ({
  innerHTML: "", textContent: "",
  setAttribute() {}, getAttribute() { return null; },
  addEventListener() {}, closest() { return null; },
  scrollIntoView() {}, dataset: {},
  querySelectorAll() { return []; }, forEach() {}
});
global.document = {
  getElementById: el, querySelectorAll: () => [], addEventListener() {},
  documentElement: el(), createElement: el
};
global.window = { console: { log() {} } };

const src = page.split("<script>")[1].split("</" + "script>")[0];
// expose the internals the harness needs
const wrapped = src + "\n;module.exports={pred,tip,laneControls,globals,STATES,base,setState,getS:()=>S};";
const m = { exports: {} };
new Function("module", "require", "global", "window", "document", wrapped)(
  m, require, global, global.window, global.document);
const A = m.exports;

let fails = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (!cond) { fails++; console.log("  FAIL  " + label); }
}
function enabledCtl(c) { return c && c.enabled === true; }
function reasoned(c) { return c && c.enabled === false && c.reason && c.reason.length > 4; }

const REQUIRED_PER_LANE = ["load", "mid", "eject", "jogFwd", "jogBack", "tagRead", "tagWrite", "assign"];

for (const key of Object.keys(A.STATES)) {
  A.setState(key);
  const S = A.getS();
  const P = A.pred(S);
  const G = A.globals(P);
  console.log("\n== " + key + " ==");

  // 1. every required control EXISTS on every lane, enabled or reasoned
  for (let i = 0; i < 4; i++) {
    const C = A.laneControls(i, P);
    for (const k of REQUIRED_PER_LANE)
      ok(C[k] && (C[k].enabled || (C[k].reason && C[k].reason.length > 4)),
         "T" + i + " ." + k + " present with a state or a reason");
    ok(C.dest.length === 6, "T" + i + " has all six destination chips");
    for (const d of C.dest)
      ok(d.enabled || (d.reason && d.reason.length > 4),
         "T" + i + " dest '" + d.label + "' enabled or reasoned");
    // no enabled control may carry an empty gcode
    for (const k of REQUIRED_PER_LANE.concat(["jogExtFwd", "jogExtBack"]))
      ok(!C[k].enabled || (C[k].gcode && C[k].gcode.length >= 2),
         "T" + i + " ." + k + " enabled implies a gcode string");
    for (const d of C.dest)
      ok(!d.enabled || (d.gcode && d.gcode.length >= 2),
         "T" + i + " dest '" + d.label + "' enabled implies a gcode string");
    // jog interlock: ACE and extruder are never both enabled
    ok(!(C.jogFwd.enabled && C.jogExtFwd.enabled),
       "T" + i + " ACE jog and extruder jog are never both enabled");
    // jog never sends the untracked primitive, never FORCE=1
    for (const k of ["jogFwd", "jogBack"]) {
      ok(C[k].gcode.indexOf("ACE_RAW_FEED") === -1, "T" + i + " ." + k + " does not use ACE_RAW_FEED");
      ok(C[k].gcode.indexOf("FORCE=1") === -1, "T" + i + " ." + k + " does not send FORCE=1");
    }
    // tag write is never enabled: this firmware has no write path
    ok(!C.tagWrite.enabled, "T" + i + " Write tag stays disabled (no firmware support)");
  }

  // 2. STOP ALL is always enabled, in every state
  ok(enabledCtl(G.stop), "STOP ALL is enabled");
  ok(enabledCtl(G.audit), "Run audit is enabled");
  ok(enabledCtl(G.supp), "Clear suppression is enabled (never gated on a state model that may be wrong)");

  // 3. stale collapses motion to unknown, never to empty
  if (key === "stale") {
    for (let i = 0; i < 4; i++) {
      const C = A.laneControls(i, P);
      ok(reasoned(C.load) && /stale/.test(C.load.reason), "T" + i + " Load refused because stale");
      ok(reasoned(C.jogFwd), "T" + i + " jog refused while stale");
      ok(A.tip(i, P).tier === "unknown", "T" + i + " tip tier is unknown, not empty");
    }
  }

  // 4. the floor rule: every fault state has an ENABLED way out
  if (key === "jam")   ok(enabledCtl(G.jamclr), "jam: Clear jam is enabled");
  if (key === "orphan") {
    let anyForce = false;
    for (let i = 0; i < 4; i++) {
      const C = A.laneControls(i, P);
      if (C.eject.enabled && /FORCE=1/.test(C.eject.gcode)) anyForce = true;
    }
    ok(anyForce, "orphan: at least one lane offers an enabled Eject (force)");
    ok(enabledCtl(G.recover), "orphan: Recover is enabled");
  }
  if (key === "suppression") {
    ok(enabledCtl(G.clearop), "suppression: Clear op is enabled");
    ok(enabledCtl(G.unlock), "suppression: Unlock toolchange is enabled");
    ok(enabledCtl(G.recon), "suppression: Reconcile target is enabled");
    ok(enabledCtl(G.supp), "suppression: Clear suppression is enabled");
  }
  if (key === "sensorfault") {
    ok(P.IMPOSSIBLE, "sensorfault: the impossible pair is detected");
    ok(enabledCtl(G.query) && enabledCtl(G.audit), "sensorfault: Query sensors and Audit are enabled");
  }
  if (key === "dryfault") {
    ok(P.DRY_FAULT, "dryfault: fault detected");
    ok(enabledCtl(G.drystop), "dryfault: Stop is enabled");
    ok(!G.dry.enabled && !G.autodry.enabled, "dryfault: starting a dry is refused");
  }
  if (key === "busy") {
    for (let i = 0; i < 4; i++) {
      const C = A.laneControls(i, P);
      ok(reasoned(C.load), "T" + i + " Load refused while the path is busy");
      if (P.PRESENT[i]) ok(/busy|occupied/.test(C.load.reason), "T" + i + " busy reason names the path");
    }
  }
  if (key === "live") {
    ok(A.tip(2, P).tier === "reckoned", "live: T2 tip is reckoned, not measured");
    ok(A.tip(2, P).tol === 18, "live: the tolerance band is the 18 mm hub poll lag");
    ok(A.tip(0, P).tier === "empty", "live: T0 is empty");
    ok(P.AUTODRY, "live: auto-dry is armed (the machine's real state)");
    ok(!G.autodry.enabled && /already holding/.test(G.autodry.reason),
       "live: Auto-dry is refused because it is already holding");
    ok(enabledCtl(G.drystop), "live: Stop is available");
    const C2 = A.laneControls(2, P);
    ok(enabledCtl(C2.jogFwd), "live: T2 ACE jog is available");
    ok(!C2.jogExtFwd.enabled, "live: T2 extruder jog is refused (tip is in the ACE segment)");
  }
  if (key === "printing") {
    ok(A.tip(2, P).tier === "inferred", "printing: tip past the gears is inferred, not measured");
    const C2 = A.laneControls(2, P);
    ok(!C2.jogFwd.enabled && !C2.jogExtFwd.enabled, "printing: jog refused in an inferred segment");
    ok(enabledCtl(C2.mid), "printing: Park is available on the loaded lane");
  }
}

console.log("\n" + (fails ? "FAILED " + fails + " of " + checks : "PASSED all " + checks + " checks"));
process.exit(fails ? 1 : 0);
