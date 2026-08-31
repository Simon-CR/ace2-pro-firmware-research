/* Render check for the ACE panel mockup.
   acceptance.js proves the control logic; this proves the markup that logic produces.
   Renders all four artboards in every state and asserts the output is well-formed and
   free of stray "undefined" / "NaN" — the two failure modes a template silently ships.

     node render-check.js
*/
const fs = require("fs");
const page = fs.readFileSync(__dirname + "/index.html", "utf8");
const src = page.split("<script>")[1].split("</" + "script>")[0];

const cache = {};
const el = id => ({
  set innerHTML(v) { cache["_html_" + id] = v; },
  get innerHTML() { return cache["_html_" + id] || ""; },
  textContent: "",
  setAttribute() {}, getAttribute() { return null; }, addEventListener() {},
  closest() { return null; }, scrollIntoView() {}, dataset: {},
  querySelectorAll() { return []; }, forEach() {}
});
global.document = {
  getElementById: id => (cache[id] = cache[id] || el(id)),
  querySelectorAll: () => [], addEventListener() {},
  documentElement: el("root"), createElement: el
};
global.window = { console: { log() {} } };

const m = { exports: {} };
new Function("module", "require", "global", "window", "document",
  src + ";module.exports={STATES,setState};")(m, require, global, global.window, global.document);

const PAIRS = [["div", /<div\b/g, /<\/div>/g], ["span", /<span\b/g, /<\/span>/g],
               ["svg", /<svg\b/g, /<\/svg>/g], ["a", /<a\b/g, /<\/a>/g]];
let bad = 0, n = 0;

for (const key of Object.keys(m.exports.STATES)) {
  m.exports.setState(key);
  for (const id of ["abCard", "abPage", "abPhone", "abKs"]) {
    const h = cache[id].innerHTML;
    n++;
    if (!h.length) { bad++; console.log("EMPTY", key, id); continue; }
    for (const [tag, o, c] of PAIRS) {
      const a = (h.match(o) || []).length, b = (h.match(c) || []).length;
      if (a !== b) { bad++; console.log("UNBALANCED <" + tag + ">", key, id, a, "vs", b); }
    }
    const stray = h.match(/.{0,50}(undefined|NaN|\[object Object\]).{0,30}/);
    if (stray) { bad++; console.log("STRAY VALUE", key, id, "-", stray[0]); }
    // every control must carry a gcode, a lane target, a step, or a door
    const btns = h.match(/class="btn[^"]*"[^>]*>/g) || [];
    for (const b of btns)
      if (!/data-(gcode|lane|step|door)=/.test(b)) {
        bad++; console.log("CONTROL WITH NO TARGET", key, id, "-", b.slice(0, 90));
      }
  }
}
console.log("\n" + (bad ? "FAILED " + bad + " problems across " + n + " artboards"
                        : "PASSED " + n + " rendered artboards: balanced, no stray values, every control targeted"));
process.exit(bad ? 1 : 0);
