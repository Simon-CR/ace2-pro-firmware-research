# A standalone control box for the ACE 2 Pro

**Added 2026-08-31.** A scoped proposal, not a design. Nothing here has been built or ordered.

The ACE 2 Pro has **no controls of its own** — no button, no display, no way to move a lane or
start a dry without a host on the other end of the RS-485 pair. That is fine while it is bolted to
an Anycubic printer. It is the central problem for everyone using one detached: on a Klipper
machine, on a bench, as a dryer, as a four-lane feeder for something that is not a printer at all.

> *"I'd like to have the option to add a physical touchscreen or something where I can physically
> control the ACE 2 unit (or multiple units from the same control box). I think as more people use
> those boxes outside their OEM intended environment, being able to control things without relying
> on Klipper would be awesome."*

There is a sharper version of the case, and it comes from Anycubic. **Their own documentation states
the ACE 2 Pro cannot dry offline** — the signal cable must be connected to a powered printer in
material-drying mode. The unit contains a well-protected PTC dryer with dual thermistors, a cascade
PID and a proper shutdown path ([07 §1](07-subsystems-and-safety.md)), and it is deliberately
unreachable unless a whole printer is switched on to babysit it. That is the gap in one sentence.

This chapter answers the architecture question first, because it decides everything else, then
scopes hardware, the control set, the hard parts, safety, and a staged plan.

**The short version:**

| Question | Answer |
|---|---|
| Can two masters share the bus? | **No.** Not for physical reasons — for state reasons. §1.2. |
| Should the box be the master, with Klipper behind it? | **Eventually yes, but not first.** §1.3–1.5. |
| What should be built first? | A standalone box on its own bus, plus a **£5 A/B switch** for people who also run Klipper. §1.4. |
| Hardware? | Pi Zero 2 W class + 800 × 480 touchscreen + an off-the-shelf CH343 USB-RS485 dongle. No PCB. §2. |
| Multi-unit? | Native in the protocol and already decoded. 2–4 units realistic, design for 8. §3. |
| Honest effort? | **40–80 hours over a couple of months** for a working single-unit box. The Klipper-bridge mode roughly doubles it and can kill a print. §7. |

---

## 0. Why this repository is the enabling asset

This is the payoff from the reverse-engineering work, so it is worth being explicit about which
document carries which part of the box. Someone starting without these would begin from hakimio's
gists and the `printers-for-people` captures — both excellent, and §7 says exactly what each gives —
but would still have to rediscover the preload runaway, the lost-STOP race, the flash-wear hazard and
the two heater commands the expensive way, on their own filament and their own flash.

| Part of the box | Document |
|---|---|
| Frame layer, CRC, the address byte, discovery and assignment | [06](06-protobuf-descriptors.md), [07 §6](07-subsystems-and-safety.md) |
| Every message shape, with six corrections to the public `.proto` | [`protocol/ace2-v1.1.31.proto`](../protocol/ace2-v1.1.31.proto), [06](06-protobuf-descriptors.md) |
| What is registered and what silently is not (67, 69, 74) | [05](05-protocol-notes.md), [06](06-protobuf-descriptors.md) |
| Dryer guards, and **the two commands that defeat all of them** | [07 §1](07-subsystems-and-safety.md) |
| The flash-wear hazard that a touch UI invites | [07 §2](07-subsystems-and-safety.md) |
| Actuator map — LEDs, fans, flaps, and their interlock bugs | [07 §5](07-subsystems-and-safety.md) |
| Motion units, the 100 mm/s ceiling, the 100 ms control tick | [08 §1](08-motion-and-preload.md) |
| **The autonomous 1700 mm preload** and its hardcoded bounds | [08 §2](08-motion-and-preload.md) |
| Slip comparator, and how to choose `check`/`error` | [08 §4](08-motion-and-preload.md), [09 §4](09-error-states-and-jam-detection.md) |
| The error vocabulary, and which codes can never reach a host | [09 §1](09-error-states-and-jam-detection.md) |
| STOP semantics, the two races, and the ~250 ms rule | [09 §5–6](09-error-states-and-jam-detection.md), [05](05-protocol-notes.md) |
| Assist admission, the ~1 s deadline, `ready` ≠ disarmed | [05](05-protocol-notes.md) |
| What the telemetry numbers actually measure | [10](10-spool-drive-and-feed-telemetry.md) |
| RFID read, write, and tag parking sequences | [03](03-rfid-and-tags.md), [04](04-tag-operations.md) |
| **The panel itself, at 800 × 480, every control in seven states** | [11 §4.4](11-operator-interface.md), [12 §9–10](12-panel-visual-design.md) |

That last row matters more than it looks. The UI for this box has, in large part, **already been
designed** — see §4.

---

## 1. The architecture — one bus, one master

The ACE has one RS-485 pair and one master. Everything below follows from that.

### 1.1 What the wire actually gives us

Three facts from [07 §6](07-subsystems-and-safety.md), and they are more helpful than expected:

* **The frame carries a device address.** The byte long described as "flags" is a static bus
  address, compared for equality; replies OR in bit 7. So addresses 0x00–0x7F, and the response
  marker is not a separate field.
* **`DISCOVER` (0) and `ASSIGN_DEVICE_ID` (1) exist and are decoded.** Multi-unit is not a feature
  we would have to invent — Anycubic built it and we can read it.
* **Nothing is persisted.** Every unit is at address 0 after every power cycle.

That last point has a consequence worth drawing out: **a single unit that has never been enumerated
lives at address 0, and any driver that does not enumerate simply talks to address 0.** Which makes
the single-unit case far more tractable than the multi-unit one, and is the hinge of §1.4.

> **Check this before relying on it.** [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) is
> reported to perform bus discovery and to run multiple ACE 2 units on a single adapter, so it may
> well assign addresses rather than sitting at 0. If it does, §1.4's switch still works but both
> masters must agree on the address map. **Read what the driver actually sends before wiring
> anything.**

### 1.2 Option B — shared bus with arbitration. **Reject it.**

The instinct is that this is an electrical problem with an electrical answer: add a token, add a
back-off, add a hardware arbiter. It is not. The physical layer is the easy half, and even solving
it perfectly leaves the design broken.

**The physical objection, stated fairly.** Half-duplex RS-485 has no collision detection. Two
masters driving the pair at once produce a differential mess; slaves drop the frame on CRC and both
masters time out. That much could be engineered around — the firmware itself already ships a
collision back-off for `DISCOVER` replies, so the idea is not alien to the protocol. If this were
the only problem, B would be merely hard.

**The objection that actually kills it is state.** The ACE's slot state machine requires exactly one
host that remembers what it started:

* **Assist state cannot be read back.** [05](05-protocol-notes.md) — the device toggles a slot
  between `assisting` and `ready` according to whether the toolhead is pulling *right now*, so
  `ready` on an armed slot means *armed but idle*. A second master polling `GET_STATUS` cannot tell
  an armed lane from a disarmed one. It will either re-arm an assist that is already running
  (`FORBIDDEN` at ~4 Hz, [09 §6](09-error-states-and-jam-detection.md)) or, worse, believe a lane is
  idle and command it.
* **There are two operation channels, and an active assist holds one indefinitely.** Two masters
  cannot both budget against a resource neither can observe.
* **STOP is not idempotent and is not observable.** [09 §6](09-error-states-and-jam-detection.md) —
  a STOP inside the 200 ms setup race is silently overwritten and still returns success. A second
  master's STOP can therefore cancel the first master's move, or appear to and not, with no way to
  tell which.
* **Motion starts with no command at all.** [08 §2](08-motion-and-preload.md) — an INSERT edge
  begins a 1700 mm autonomous preload. Both masters see the result and neither caused it.
* **The reaction budget is ~1 s.** [05](05-protocol-notes.md) — `ASSIST_ERROR` arrives about a
  second after the toolhead stops pulling. An arbitration scheme that can hold the bus for longer
  than that is not merely inefficient; it grinds filament.

So the honest verdict is not "difficult". It is: **the device is not designed to be shared, and no
amount of bus arbitration fixes a state model that only one host can hold.** Two masters on this
bus is a split-brain problem wearing an electrical costume.

### 1.3 Option C — the box is master, Klipper talks to the box. Challenged.

C is the attractive answer and it is the right *destination*. It is not the right *starting point*,
and three objections should be on the record before anyone builds it.

**Objection 1 — it puts a new device in the critical path of a print.** Today a failure of the
Klipper host is the only way a toolchange dies. Under C, a box that reboots, fills its SD card, or
hangs takes the print with it — and can take it *while an assist is armed*, which is the one state
where nothing stops on its own. That is a real reliability regression bought for a convenience gain,
and it must be argued for, not assumed.

**Objection 2 — latency, and this one is measured.** `FILAMENT_PATH_FIXES.md` records that an
`ACE_RAW_FEED` mid-print blocks the Klipper motion queue for the RS-485 round trip badly enough that
*a 50 mm leg at 15 mm/s visibly stalled the toolhead on layer 2*. The round trip is already the
bottleneck. Adding a hop is acceptable over USB-serial, where a byte-level relay costs single-digit
milliseconds. **It is not acceptable over the network.** So the network variant of C — "Klipper
points at the box's HTTP API" — should be ruled out now, before someone builds it because it is the
easier one to write.

**Objection 3 — C recreates B inside the box.** The box would hold two command streams and must
serialise them. That *is* solvable, unlike B, because there is now a single owner of the bus, a
single serialisation point, and visibility of both streams and their echoed request ids. But it is
the majority of the work, not a detail: it means modelling per-lane ownership, honouring the
two-channel limit, and deciding what happens when the operator presses a physical STOP on a lane
Klipper is mid-toolchange on. (The answer is that the physical stop wins — it is a person standing
at the machine — but Klipper will not find out until its next poll, because **the protocol is
strictly request/response with no unsolicited-message path**. There is no way to interrupt the host.
That is a stated cost, not a bug.)

**Transparency.** For an existing driver not to notice, the box must present a USB-serial device
speaking the ACE protocol byte-for-byte, including the fixed 5-unit reply delay and the address/bit-7
convention. That is achievable. The alternative — adapt the driver — is a stated cost and a fork to
maintain against [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO).

### 1.4 Option D — the one the brief did not list, and the one to build first

**Put a switch on the pair.**

Both masters want address 0. A single unit is at address 0. So for the single-unit case — which is
almost everybody — an A/B switch on A/B/GND (a manual DPDT, or a relay the box drives) gives the
operator both worlds for the price of a connector, with **zero protocol work and zero split-brain
risk**. Only one master is ever electrically present. Klipper sees an unplugged ACE while the box
has it, which is a state its driver already handles because it is what a loose cable looks like.

This is not a compromise position. It is strictly better than option A — which is the same thing
with the operator crawling behind the machine — and it delivers the standalone box on day one
without the print-killing failure mode of C.

> **One bench test decides how well D scales past one unit:** *does a unit that has already been
> assigned an address still answer `DISCOVER` at address 0?* If yes, a new master can re-enumerate
> after the switch flips and D works for any number of units. If no, switching masters requires
> power-cycling the units first. **This is untested.** It is a fifteen-minute experiment and it
> should be run before anything else in §3 is designed.

### 1.5 Verdict

| Option | Verdict |
|---|---|
| **A. Standalone only** | Correct, and unglamorous. Superseded by D at negligible cost. |
| **B. Shared bus with arbitration** | **Reject.** Not an electrical problem — a state problem that arbitration cannot reach. |
| **C. Box is master, Klipper behind it** | **Right destination, wrong start.** USB-serial only; never network. Attempt after §7 stage 3 has run for months. |
| **D. Box is master, bus switched** | **Build this.** Delivers the stated goal immediately, risks nothing, and leaves C available later. |

**So: C is not strongest today.** It is strongest in a year, and the fastest route to it is to build
the standalone box behind a switch first — because every part of C except the arbitration layer is
the standalone box.

---

## 2. Hardware

### 2.1 Compute and display

| | ESP32-class + SPI display | **Pi Zero 2 W class + 800 × 480 touchscreen** | Headless + phone |
|---|---|---|---|
| Parts | ESP32-S3 dev board with integrated LCD, ~£15–40 all-in | Pi Zero 2 W ~£15, DSI or HDMI touchscreen ~£35–60, PSU + card ~£15 | Pi Zero 2 W + PSU + card, ~£30 |
| Total | **£20–40** | **£70–110** | **£30** |
| Boot | instant | ~25 s | ~25 s |
| Robustness | no filesystem to corrupt; survives a yanked plug | SD cards die from unclean power-off; needs a shutdown story | same |
| **UI reuse** | **none** — every screen reimplemented in LVGL/C++ | **[11 §4.4](11-operator-interface.md) and [12](12-panel-visual-design.md) are directly buildable** as the same web page in a kiosk browser | same reuse, no screen |
| Protobuf | nanopb, fine, but hand-plumbed | any language; the `.proto` compiles as-is | same |
| Verdict | best *product*, worst *project* | **recommended** | **cheapest proof; build this first** |

The decisive argument is the third row from the bottom. This repository already contains a
fully-specified 800 × 480 landscape panel — component inventory, seven states per control, refusal
strings, real pixel dimensions. On a Linux SBC that specification is buildable more or less as
written. On an ESP32 it must be redrawn in LVGL, which is a second project stapled to the first.

An ESP32 becomes the right answer *later*, if this ever turns into something people buy rather than
build — instant-on and unkillable-by-power-cut are genuinely better properties for an appliance. It
is the wrong answer for getting to a working box.

**Recommended path: prove the software headless on a Pi Zero 2 W serving its own page over Wi-Fi
(£30), then add a screen to the same machine (£70–110) once the software is worth looking at.** The
screen is the last thing to buy, not the first.

### 2.2 RS-485 — transceiver, termination, biasing

**For stages 0–3, buy nothing new: use a CH343 USB-RS485 dongle** (~£8) — and note that **Anycubic
sells the ACE 2 Pro USB-to-RS485 signal cable as a separate part**, so the connector problem is
off-the-shelf rather than a soldering job. The dongle is the known-good host side: it is what every
finding in this repository was measured through. Plugging it into a Pi is the entire hardware design
for the first three stages, and it removes the single most common failure class before it can happen
(see the direction-control note below).

For an integrated board later, the part class matters more than the part:

| Requirement | Why | Parts |
|---|---|---|
| **3.3 V logic** | Pi and ESP32 GPIO are not 5 V tolerant | SN65HVD72, SN65HVD75, MAX3485, ADM3485, THVD1450 |
| **True fail-safe receiver** | An undriven pair floats and the UART sees noise as start bits. A fail-safe receiver holds the output high on an idle, open or shorted bus — **this replaces the bias-resistor network entirely** | SN65HVD7x family, THVD1450 |
| **Slew-rate limiting** | 230 400 baud is slow; limited slew cuts EMI and reflection sensitivity at no cost | SN65HVD72 (250 kbps limited) suits this exactly |
| **High ESD rating** | The cable runs between separately-powered boxes on a bench | THVD1450 (±18 kV IEC) |
| **Auto-direction, ideally** | See below | MAX13487E (no DE pin at all) |

> **The direction-control trap, named so it can be avoided.** On a half-duplex transceiver the DE
> line must be released within about one bit time of the last stop bit — **~4.3 µs at 230 400** — or
> you clip the slave's reply and the bus looks broken. Doing that from software on a Linux SBC is
> unreliable. Three ways out, in order of preference: a USB dongle that handles it in hardware
> (stage 0–3); an auto-direction transceiver with no DE pin; or a UART with a hardware RS-485 mode
> that drives RTS itself, which the ESP32's UART has and the Pi's PL011 does not.

**Termination.** 120 Ω across A/B at **the two physical ends of the bus only** — never per node,
never in the middle. Being straight about the physics: at 230 400 baud a bit is 4.3 µs and cable
propagation is roughly 5 ns/m, so on any bench-scale run reflections settle in well under 1 % of a
bit time. **Termination is electrically optional here.** For one unit on a 1 m cable, leave it out.
For a chain across a bench, fit it at the ends — it costs 2p and removes a variable.

**Biasing.** This is the one that actually bites, and it is not reflection: it is idle-line float
producing garbage into the receiver between frames. Fix it by **choosing a fail-safe receiver**, not
by adding bias resistors. If a non-fail-safe part is already on the bench, bias once — pull-up on A,
pull-down on B, ~560 Ω–1 kΩ — **at one point on the whole bus, not at every node**, because each
bias network loads the pair.

**Ground is not optional.** RS-485 is differential but has a ±7 V common-mode window. Two boxes on
separate supplies with no ground reference between them can leave it, and the symptom is
intermittent corruption that looks like a software bug. Run a third conductor. And keep the
earth-versus-DC-ground distinction straight — that trap has already cost time on this machine once,
during the RDM pinout work.

**Cable and topology.** Shielded twisted pair for A/B plus a ground conductor; shield to earth at one
end only. **Daisy-chain, in and out of each unit, with stubs as short as possible. Not a star.** A
star has reflection points that cannot be terminated. At 230 400 a short star would probably work,
which is precisely why it should not be designed in — it will fail on someone else's longer cable.

### 2.3 Power

**The box does not power the ACEs.** Each unit needs its own supply regardless — there is a PTC
heater in there, and the dryer is the largest load in the system.

**Give the box its own supply.** The tempting alternative — buck 24 V down from one unit's supply —
couples the box's life to a unit that may need power-cycling, and power-cycling is the documented
recovery for a latched `ptc_error`/`ntc_error` ([07 §1](07-subsystems-and-safety.md)). Losing the
panel at the exact moment you are recovering a heater fault is the wrong trade for one fewer plug. A
separate supply also makes the ground-reference question in §2.2 a deliberate decision rather than an
accident.

Budget: 5 V 1 A for a headless Pi Zero 2 W, 5 V 2.5 A with a screen, 5 V 0.5 A for an ESP32.

> **Unknown, and it should be measured before any enclosure is designed:** the ACE 2 Pro's own
> RS-485 connector pinout, and whether that port carries usable power. **This repository does not
> document it** — every measurement here was taken through a USB dongle on the other end of a cable.
> It is a meter-and-notebook job of the same shape as the RDM pinout work, and it belongs in stage 0.

---

## 3. Multi-unit

### 3.1 How it actually works

From [07 §6](07-subsystems-and-safety.md):

1. **Broadcast `DISCOVER` (cmd 0) to address 0.** Every unassigned unit replies with its three
   STM32 UID words. Replies are spread by a **random 0–40 unit back-off seeded from the UID** —
   every other command replies at a fixed 5. That back-off exists for exactly one reason: multiple
   unassigned units all answering address 0.
2. **`ASSIGN_DEVICE_ID` (cmd 1)** with `{uid1, uid2, uid3, device_id}`. Only the low byte is used and
   bit 7 must stay clear, so **addresses 0x01–0x7F**. On a UID mismatch the unit returns code 1 and
   **transmits nothing at all** — so the host must treat silence as a distinct outcome, not a
   timeout.
3. **Allow ~200 ms to settle**, then talk to the unit at its new address.

### 3.2 The requirement that falls out of "nothing is persisted"

**Every unit returns to address 0 on every power cycle.** Units get power-cycled independently — and
must be, to clear a latched dryer fault. So the box cannot enumerate once at boot and cache the map.

**It needs continuous re-discovery discipline:** when a unit stops answering its assigned address,
the correct response is *re-run discovery*, not *declare it dead*. And a unit that has just come back
at address 0 will collide with any other unassigned unit, so the recovery path is the discovery path.
This is non-obvious, it is a direct consequence of a documented fact, and a box that gets it wrong
will appear to randomly lose units.

### 3.3 How many units is realistic

Three candidate limits, and the interesting one is not the obvious one.

* **Electrical:** standard RS-485 is 32 unit loads; 1/8-load transceivers reach 256. Not the limit.
* **Addressing:** 127 addresses. Not the limit.
* **Bandwidth:** a `GET_STATUS` round trip is roughly 5 ms of wire time at 230 400 plus the fixed
  reply delay and host turnaround — call it 10 ms per unit per poll, conservatively. Eight units at
  4 Hz is ~32 % bus duty. Not the limit either.
* **The actual limit is the reaction budget.** `ASSIST_ERROR` fires ~1 s after a pull stops
  ([05](05-protocol-notes.md)). Any lane the box has armed must be polled fast enough to disarm
  inside that window, and every unit on the bus shares the same wire. **Poll rate is budgeted
  against the assist deadline, not against bandwidth** — which is a much tighter constraint and a
  much better design rule.

**Practical answer: 2–4 units is the realistic target; design the addressing and polling for 8.**
Beyond that the operator's ability to reason about 32 lanes fails before the bus does. That target is
corroborated from outside: [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) reports running
**3 units / 12 colours on a single adapter**, and [decay71/multiACE](https://github.com/decay71/multiACE)
tops out at 4 units / 16 colours. Two independent projects landed on the same ceiling.

> **Do not design §3 in ignorance of ACEPRO.** It already implements bus discovery and multi-unit on
> one adapter. Whatever it does is the de-facto convention, and a box that disagrees with it will be
> incompatible with the one driver most people run. Read it first; deviate only deliberately.

> **Caveat on the back-off:** 0–40 "units" is presumably 0–40 character times, ~1.7 ms of total
> spread at 230 400. That is a narrow window for several simultaneous unassigned units, and the unit
> of measure is **inferred, not confirmed**. So enumeration is retry-based by nature, and the safe
> commissioning procedure is to **assign addresses one unit at a time** — power up, discover, assign,
> next — rather than hot-plugging five and hoping.

---

## 4. The minimum viable control set

### 4.1 The honest reduction — say this before drawing anything

[12 §9](12-panel-visual-design.md) specifies a lane as a **path ladder** with five landmarks: ACE
edge, hub, toolhead entry, gears, melt zone. It is the right design and it should be reused.

**But four of those five landmarks are outside the ACE.** `hub_detect` is a sensor added to this
machine; entry and post-gear are toolhead switches; the melt zone is *derived* from Klipper state and
has no switch at all. A standalone box has none of them.

**Standalone, the ladder honestly collapses to two landmarks:** *in the lane* (measured — INSERT is a
real ADC channel, and the lane's own quadrature encoder measures travel) and *somewhere past the ACE
outlet* (reckoned from commanded length, with **no confirmation at the far end at all**).

That is a large reduction and it must be shown, not hidden. [12 §9.2](12-panel-visual-design.md)'s
confidence tiers exist for exactly this: past the outlet the box draws a **hatched band across the
whole segment with no dot**, because a dot is a claim of position the sensors do not support. A
standalone panel that draws a confident tip position is reproducing the precise lie doc 12 was
written to remove.

Two further things the box simply cannot know:

* **Per-lane runout.** The four EMPTY sensor channels are stubbed to constant 0 in V1.1.31 and can
  never fire ([08 §6](08-motion-and-preload.md)). Runout needs a sensor the box does not have.
* **Anything downstream.** No toolhead, no extruder, no melt zone.

### 4.2 What a first version must do

Everything below is reachable from the ACE alone.

**Unit strip, one per ACE**
- Connection, assigned address, chamber temp / RH (from the unit, not a second sensor —
  [12 §10.2](12-panel-visual-design.md)), dryer state and target.
- **`STOP ALL`** — `STOP_FEED_OR_ROLLBACK` on every lane of every unit, then **poll `GET_STATUS`
  until each slot reads 0** before the button says "stopped". Not fire-and-forget; see §5.3.

**Lane tile, ×4**
- Presence from the INSERT channel (`GET_KEY_STATE`, cmd 73). Buffer state from BUF_RST / BUF_BACK /
  CHN_BUF_FEED on the same call.
- Material, colour, SKU from the RFID record (`GET_FILAMENT_INFO`, cmd 13).
- Slot status **by name**, including error states — and only the ones that can actually occur (§5.2).
- **Live slip**: `GET_FEED_INFO` (cmd 76) returns commanded-vs-encoder per lane. Showing
  `|length − decoder|` live is something no existing panel does, it is free, and it is the single
  most useful diagnostic number the device produces. [10](10-spool-drive-and-feed-telemetry.md)
  explains why `magnitude_mm` and `moved_mm` are different quantities.

**Per-lane motion**
- **Feed / retract / eject** as bounded moves. Speed capped — 100 mm/s is the hard firmware ceiling
  ([08 §1](08-motion-and-preload.md)) and the panel should sit well under it.
- **Jog**, exactly as argued in [12 §9.4](12-panel-visual-design.md): fixed steps (1 / 5 / 25 /
  100 mm), no free numeric entry, **no latching motion** — press-and-hold moves, release stops.
- **Assist: heavily guarded, and possibly absent — see §5.1.** It is not a normal control on a
  standalone box.

**RFID**
- **Scan** — whole-unit, not per-lane (two readers, `reader = lane >> 1`, and two tags in range give
  ANTICOLLISION — [08 §2](08-motion-and-preload.md)).
- **Read** per lane.
- **Write** per lane, **only on units running the patched firmware** ([01](01-firmware-patches.md),
  [04](04-tag-operations.md)). The box can detect that: UID passthrough reports `version 0x0201`. A
  write changes physical media irreversibly and keeps a confirm step naming what goes to which spool
  — the one place friction is added deliberately.

**Dryer** — full control, per [12 §10](12-panel-visual-design.md)
- **One-shot**: preset chips → `DRYING` (cmd 11) with temp and minutes. **No free-text temperature
  field, anywhere.** Accepted range is 15–65 °C and nothing else.
- **Humidity hold**: the ACE has no such mode. `ACE_AUTODRY` is a Klipper macro, so **the box must
  run the controller itself** — read RH, decide, command. That is new code, not a passthrough.
- **Off**, and `Stop` is never disabled while anything is running.
- Guards per [12 §10.4](12-panel-visual-design.md), plus the standalone-specific ones in §5.4.

**Optional and cheap: light the lane.** The four slot LEDs are directly addressable
([07 §5](07-subsystems-and-safety.md)). Lighting the lane you are about to move is a genuinely good
physical affordance and costs one command — but use the component driver path, **not `FLASH_LED`
(70)**, which holds a suppression mask that makes `SET_FAN` and `SET_VALVE` silent no-ops with no way
to cancel.

---

## 5. What is genuinely hard

Five things. A box that does not model them will hurt filament, and one of them will destroy the
unit's flash.

### 5.1 Assist, on a machine with no extruder

The brief names the buffer/assist state machine, and it is worse than it looks in the standalone
case.

Modes 2 and 3 exist to **follow an extruder**. Their stop conditions are buffer switches: BUF_RST
stops mode 2, BUF_BACK stops mode 3 ([05](05-protocol-notes.md)). With nothing holding the strand —
no bowden, or an open idler, which is the normal state of a detached unit — **neither stop condition
ever occurs, and the mode runs continuously.** Mode 2 pushes filament until it buckles. Mode 3 rewinds
until it goes taut or forever.

Three compounding facts:
* The `speed` and `length` you pass are **ignored** — the handler hardcodes unbounded length at
  50 mm/s.
* `ASSIST_ERROR` arrives ~1 s after the pull stops, not at the MCU's 4 s backstop. Design against 1 s.
* **`ready` does not mean disarmed.** The box must track assist state itself and never infer it.

**Position: on a standalone box, assist is not a toggle.** Offer it only as momentary press-and-hold,
with STOP issued on release *and* on a hard ~800 ms watchdog regardless. Or omit it from v1 entirely
and lose nothing — it is a feature for a machine that is printing.

### 5.2 The preload — the box cannot prevent it, and it is triggered by the most common action

On a clean 0→1 INSERT edge the firmware sets `PRELOADING` and runs a **1700 mm budget at 50 mm/s —
34 seconds of continuous feed** — entirely autonomously ([08 §2](08-motion-and-preload.md)). No host
command starts it. There is no command to disable it.

Worse: an **undecodable** tag makes it run *longer* than no tag at all, because the reader keeps
answering and never reaches a terminal state. A tagless spool stops early; a foreign tag runs the
full budget and then retracts 1300 mm — about 60 seconds of motion, which is a real logged incident
on this machine.

**And inserting filament is the single most common thing an operator does.** So this is the largest
"hurt filament" risk in a standalone box, and it is triggered by a human hand, not by the UI.

What the box can actually do, in order of value:

1. **Send `SET_PRINTER_STATUS = 0`.** This caps the preload at **750 mm** and suppresses the
   retract-to-datum step ([08 §2](08-motion-and-preload.md)). For a detached unit that is almost
   certainly the right default, and it more than halves the runaway. The cost is documented: with
   status 0 the ACE stops re-reading tags on filament motion and remaining-length tracking degrades
   ([07 §3](07-subsystems-and-safety.md)). For a box whose job is manual control, that is a good
   trade — but it should be a visible setting, not a hidden one.
2. **Detect `PRELOADING` and say so, loudly**, with a stop control already under the finger.
3. **Do not promise to stop it cleanly.** STOP on status 5 clears only if it is still 5, and the
   preload is a 12-state machine. Honest UI: *"the unit is loading this lane by itself"*.

### 5.3 STOP is not reliable, and a stop button that lies is worse than none

From [09 §5–6](09-error-states-and-jam-detection.md) and [05](05-protocol-notes.md):

* There is **one** stop opcode; "stop feed", "stop unwind" and "stop assist" are the same command.
* It **returns success regardless** — including in the states where it does nothing at all.
* Two provable races drop it: a pre-dequeue race (the move then runs its **full commanded length**),
  and a 200 ms `vTaskDelay` in the move-setup path.
* On statuses 131 / 133 / 134 / 135 it does **nothing whatsoever**.

Two rules for the box, and they shape the UI:

* **Never issue STOP within ~250 ms of the `FEED_OR_ROLLBACK` that started the move.** The firmware
  will drop it. A touch button that fires immediately after a jog will do exactly this.
* **Always verify by polling `GET_STATUS` to 0.** So the stop control has three states —
  `stop` → `stopping…` → `stopped` — and must never flip straight to "stopped" on the ack.

### 5.4 Errors: a smaller vocabulary than the driver's, and STOP does not clear them

[09 §1](09-error-states-and-jam-detection.md) is unambiguous and the box must follow it:

* **133 `stuck` and 134 `tangled` can never reach a host** in V1.1.31 — computed, then re-coded to
  129 or 132 by the operation handlers. **The box must not display "stuck" or "tangled".** Every
  panel that does is showing a state that cannot occur.
* **130 `rollback_error` is unreachable** — rollback sets the bypass flag for the whole move.
* So the real vocabulary is **129 feed, 131 assist, 132 preload, 135 motor**. Four words.
* **STOP does not clear any of them.** The only recovery is starting a new operation on that slot
  (every mode is admitted when status ≥ 127) or a power cycle. **There is no clear-error command.**

So the box needs an explicit affordance — and it should be labelled for what it is. A "Clear error"
button that secretly issues a zero-length feed is fine; one that implies the error was acknowledged
is not.

### 5.5 The flash-wear hazard — the one that destroys hardware, and a GUI invites it

This is not in the brief's list and it is the most dangerous item here.

`SET_SLOT_STATUS` and `SET_MATERIAL_NAME` each commit **two full 2 KB page erases plus two 1064-byte
programs**, with interrupts masked for ~40 ms. There is no journal, no wear levelling, no
compare-before-write, and **both redundant copies are erased in the same commit**. Worse, both
commands preload the debounce counter *past* its own flush threshold, so **every call commits within
~10 ms even when the value is unchanged.** STM32F1 endurance is ~10 000 cycles per page.
([07 §2](07-subsystems-and-safety.md).)

**A touchscreen material picker that writes on every tap will destroy the settings page**, and with
it the key calibration and per-slot records. The mainline Klipper driver never sends either command,
so this hazard belongs entirely to hand-written tooling — which is exactly what this box is.

**Rule: the box either does not offer these, or it batches them behind an explicit `Save` with a
change comparison.** Never on a picker, never on a slider, never on a poll.

### 5.6 And the smaller ones, listed so they are not rediscovered

* **`LINEAR_KEY_CALIBRATE` (15) is permanent with no factory reset.** One accidental tap can
  misreport filament presence on a channel *forever*; recovery requires deliberately collapsing the
  pair and rebooting. Either omit it or put it behind a typed confirmation — never a plain button.
* **`FILAMENT_IDENTIFY` (68) returns `FilamentInfoResponse`, not `GenericResponse`** — the driver bug
  in [05](05-protocol-notes.md). New code should get this right from the start.
* **`SET_FEED_CHECK` bounds**: `error_length ≤ check_length`, both 3–254, `error ≠ 255`. The widely
  quoted `check=80, error=90` would be **rejected**. And the values **cannot be read back** — so if
  the box sets them, it must record what it set.
* **Commands 67, 69 and 74 are not registered** and return code 400. Do not put buttons on them.
* **`SET_FAN` ignores its `fan1`/`fan2` selectors in PWM mode**, and `speed = 0` does not stop a
  running PWM timer.

---

## 6. Safety

This box commands motion and a heater with **no printer supervising it**. That is a different
liability from a Klipper panel, and it deserves its own section.

### 6.1 What must not exist in the box at all

Not disabled. **Absent** — no code path, no debug menu, no raw-command console that can reach them.

| Command | Why |
|---|---|
| **`SET_DRY_POWER` (65)** | Writes the heater triac duty directly with no state check, no dryer gate and no temperature reference. **With the dryer stopped, `SET_DRY_POWER(100)` energises the heater at full duty, fans off, no timer, and nothing in the firmware will ever switch it off.** |
| **`SET_PTC_TEMP` (75)** | Installs a PID target with no upper bound and sets the setpoint to 0 — and the fault-shutdown body is guarded by `setpoint > 0`, so it leaves the unit heating **with the thermistor protections disarmed**. |
| **`MOTOR_TEST` (77)** | Validates index, speed and mode but leaves `length` **unbounded**, never reads printer status, and permits every error state ≥ 0x80 — it will drive a lane the firmware has already declared jammed. |
| **`SET_OUTPUT` (72)** | Raw GPIO write with no bounds that **never arms the flap watchdog** — `SET_OUTPUT(0xC0, 1)` stalls both flap motors indefinitely. |

The dryer's sanctioned path (`DRYING`, cmd 11) is, by contrast, **genuinely well protected and safe
to run unattended**: hard 15–65 °C ceiling, dual thermistors with the hotter one governing, a
cross-thermistor slope check, a 5-minute no-rise timeout, a hardware fault input, a 90 % duty cap,
and a shutdown that always runs at end of cycle. The whole risk is the two debug commands. Keep them
out and the heater is not the problem.

### 6.2 What the box must refuse to do

* **No motion on a lane whose state is unknown or stale.** [12 §9.4](12-panel-visual-design.md)'s
  rule, and it applies harder here: jogging blind is how actuators get out of step.
* **No second operation on a unit with one in flight.** There are two op channels and an assist holds
  one; the box must model the limit rather than discover it as `FORBIDDEN`.
* **No free-text temperature, no free numeric jog distance.** Presets and fixed steps only.
* **No latching motion control anywhere.** Every continuous move is press-and-hold with a watchdog.
* **No re-issuing `DRYING` to change a running cycle.** It changes temperature and duration but
  **not the start timestamp**, so `DRYING(60, 240)` sent three hours in expires immediately. A hold
  controller must stop, then start.
* **Never trust a dryer ack.** `ptc_error` and `ntc_error` latch permanently, and a new `DRYING` on a
  latched unit is **acknowledged as success and silently does nothing**. Verify via `GET_STATUS` /
  `GET_TEMP` that the temperature is actually rising. Only a power cycle clears the latch.

### 6.3 Loss of contact mid-operation — the honest answer

**The ACE has no comms-loss timeout.** Nothing in the UART task touches the dryer state, and nothing
stops a feed because the host went away. So:

* **If the box dies, motion continues.** A commanded feed runs its full commanded length. An armed
  assist runs until a buffer switch stops it — and on a free strand, no buffer switch ever will.
* A dry cycle **completes safely on its own timer**. That is the one comforting case.

This is a property of the hardware, not a design choice, and there are only three responses:

1. **Never issue an unbounded move unless a finger is on the button.** Bounded feeds only; assist
   only under press-and-hold with a watchdog. This is the whole mitigation, and it is why §5.1 comes
   down where it does.
2. **On reconnect, stop before anything else.** The box's recovery path is: `STOP_FEED_OR_ROLLBACK`
   every lane on every unit → poll each status to 0 → re-enumerate (§3.2) → *then* resume the UI.
   Never restore a previous state; the world may have moved.
3. **Say plainly, in the box's own documentation, that its STOP button is not an emergency stop.**
   The emergency stop is the ACE's power switch, and it must be within reach. A box that implies
   otherwise is more dangerous than one with no stop button, because the operator will reach for the
   wrong thing.

---

## 7. Prior art

Checked before proposing this, because duplicating someone's project silently would be
unforgivable given how much of this repository is other people's work.

### 7.1 What exists

| Project | What it is | Overlap |
|---|---|---|
| [**hakimio's `ace2-pro-shell.py`**](https://gist.github.com/hakimio/551915aa02b7e248721bed672ad46e0b) | **The real precedent, and the closest thing that exists.** An interactive PC shell driving an ACE 2 Pro over the USB-RS485 adapter with **no printer at all**: discovery and device-ID assignment for daisy-chaining, feed/rollback/assist, drying, fans, valves, LEDs, RFID, the 17-bit sensor mask, feed encoder. Three modes including a passive bus listener. | **This proves headless control works.** It is a developer REPL — no GUI, no display, no hardware, no service. It is the thing a box would be built *on top of*, not a box. |
| [hakimio's ACE 2 research gist](https://gist.github.com/hakimio/4916ff69add458fdc51aeea76f21efb9), [`ace2-ota-update.py`](https://gist.github.com/hakimio/39c71fa7174e699c6470b7c79323b189) | The primary source for the entire protocol; the OTA flasher likewise runs standalone. | The enabling work. Not a competing box. |
| [hakimio/U1-Ace `ace2`](https://github.com/hakimio/U1-Ace/tree/ace2) — `src/ace2_protocol.py` | The packet layer, and **explicitly transport-agnostic** — its own header says *"pure data: no serial, no threading"*. Implements `DISCOVER_DEVICE` / `ASSIGN_DEVICE_ID`. | The most reusable piece of prior art anywhere. **See the licensing correction below before touching it.** |
| [**printers-for-people/ACEResearch**](https://github.com/printers-for-people/ACEResearch) | The foundational RE repo: `HARDWARE.md`, `PROTOCOL.md`, raw captures — and an ACE **emulator** (emulates a unit for development, i.e. the opposite direction from a master). **CC0.** | Not a controller, but **the most permissively licensed protocol material in the ecosystem**, and the emulator is exactly the test rig stage 4 would need. |
| [Kobra-S1/ACEPRO](https://github.com/Kobra-S1/ACEPRO) | The maintained Klipper driver, GPL-3.0. **Already does ACE 2 over RS-485 with multiple units on one adapter and bus discovery** — reported tested at 3 units / 12 colours. Ships a KlipperScreen panel and a browser dashboard at `/ace.html`. | Requires Klipper — its README says so explicitly. **But it is the existing multi-unit implementation, and §3 should be checked against it rather than designed in ignorance of it.** Its KlipperScreen panel is the closest UI prior art, reviewed in [11](11-operator-interface.md). |
| [decay71/multiACE](https://github.com/decay71/multiACE) | GPL-3.0, actively maintained. 1–4 units, 16 colours, reactive web UI at `/multiace/` with a command-queue editor, loadouts, dryer settings, parked swaps. | Requires a Snapmaker U1. **The best existing multi-unit UI** — read it before drawing screens. Cited for *delivery* in [11 §0](11-operator-interface.md). |
| [DnG-Crafts/U1-Ace](https://github.com/DnG-Crafts/U1-Ace) | **Correction to the brief: this is not a flashing utility.** It is a set of Klipper `extras` modules for the Snapmaker U1 with live feed/retract/assist and drying. | Requires the U1 printer. Cannot run standalone. The "GUI flashing utility" description belongs to the same author's ACE-RFID. |
| [DnG-Crafts/ACE-RFID](https://github.com/DnG-Crafts/ACE-RFID) | Anycubic tag page layout, plus an Android app, a Windows GUI and ESP32/Pico sketches — **all of which write NTAG/Ultralight tags and never speak to the ACE at all.** | Data and a tag writer. Credited in [03](03-rfid-and-tags.md). |
| Gen-1 tooling — ValgACE, DuckACE, BunnyACE, ACEPROSV08 | The first-generation protocol: USB serial, 115200, custom binary frames. ValgACE is feature-complete and frozen by its author's own statement. | **None can talk to an ACE 2 at all.** Not prior art. |

### 7.2 The nearest structural analogues, all on other hardware

Worth reading before designing anything, because each one has already made the mistakes:

* [**druckgott/bambulab_ams_diy_esp32**](https://github.com/druckgott/bambulab_ams_diy_esp32) — an
  **ESP32 with a MAX485 speaking RS-485 to a genuine Bambu AMS**. Structurally this is the closest
  thing in existence to the proposal, one vendor across. It reads as an analysis rig rather than a
  finished controller, and it has no display. No stated licence.
* [**BMCU / BIQU BMCU-370**](https://github.com/karlingen/BMCU) — an open-source AMS-Lite replacement
  on a CH32, now sold commercially. The precedent for *a community RS-485 device on a printer's
  filament bus becoming a real product* — though it **emulates** an AMS rather than driving one.
* [**liulei732/Bambu-Panel**](https://github.com/liulei732/Bambu-Panel) — ESP32 with an
  **800 × 480 touchscreen** and an AMS page for two units. The form-factor precedent, and a reason
  to take §2.1's ESP32 option seriously as a *later* product even though it loses on §2.1's terms
  today. It commands over MQTT; it is not a bus master.
* [**KlipperScreen-Happy-Hare-Edition**](https://github.com/moggieuk/KlipperScreen-Happy-Hare-Edition)
  — the MMU-touchscreen UX precedent, and a cautionary one: it exists as a *fork* because
  KlipperScreen *"lacks the level of panel integration and features needed"*. Neither Happy Hare, Box
  Turtle, AFC-Lite nor ERCF has a standalone hardware panel; all are Klipper-hosted.
* ESP32 filament dryers with TFT displays are a **well-trodden form factor** (`itarozzi/diy_filament_dryer`,
  SiloCityLabs' ESPHome dryers). Nobody has pointed one at an ACE.

### 7.3 What does not exist

Stated plainly, because it is the justification for the whole proposal:

1. **No embedded RS-485 master for an ACE**, either generation. No ESP32, RP2040, STM32, ESPHome
   component or Home Assistant integration. Every ACE driver in existence is Python, running either
   inside Klippy or as an ad-hoc PC script.
2. **No physical enclosure, display or button panel for an ACE.** Nothing on GitHub, Printables,
   Thingiverse or Cults. Printables ACE content is shelves, risers and a USB adapter.
3. **No daemon or service that owns an ACE with no printer stack.** hakimio's shell is an
   interactive script, not something that runs unattended.
4. **No offline drying with a UI** — vendor-blocked, community-unaddressed.
5. **Multi-unit addressing outside a printer context.** `ASSIGN_DEVICE_ID` is implemented twice, and
   both are printer-coupled or a REPL.

So this is not a duplicate. It is also not a gap nobody noticed: it was closed to everyone until the
protocol was decoded, and the decoding is barely a month old.

### 7.4 A licensing correction that matters, and a courtesy owed

**The brief's premise that `ace2_protocol.py` is GPL-3.0 appears to be wrong, and in the more
awkward direction.** GPL-3.0 covers `SnapmakerU1-Extended-Firmware`. The canonical copy of
`ace2_protocol.py` lives in [`hakimio/U1-Ace`](https://github.com/hakimio/U1-Ace/tree/ace2), which —
like its upstream `DnG-Crafts/U1-Ace`, and like all of hakimio's gists — **carries no licence file at
all.** Legally that is all-rights-reserved, which is *more* restrictive than GPL, not less.

The practical conclusion is unchanged and if anything firmer: **read it, understand it, reimplement
it; do not copy it.** But there is an action attached. hakimio is reachable and actively publishing,
and the ecosystem would be better off if that file had a licence on it. **Ask him.** He has given
this community more than anyone, and the least anyone can do is help him make his work formally
reusable.

For clean-room protocol facts with no licence question at all, `printers-for-people/ACEResearch` is
**CC0** and is the right starting point.

---

## 8. Staged plan

Each stage is independently useful and independently abandonable. Effort figures are for someone who
tinkers competently rather than someone who does this professionally.

### Stage 0 — prove the wire. **A weekend.**

A CH343 dongle, a Pi or a laptop, one ACE. Build frames, talk, read back. **Success looks like
`GET_STATUS`, `GET_KEY_STATE` and `GET_FEED_INFO` decoding correctly, and one bounded 50 mm feed
that stops where it should.** No display, no UI, no multi-unit.

Also in stage 0, because both are cheap and both unblock later decisions:
* **Meter the ACE's own RS-485 connector** and record the pinout and whether it carries power (§2.3).
* **Run the option-D test**: does an assigned unit still answer `DISCOVER` at address 0? (§1.4).

Risk: low. Worst case is a wasted weekend and two facts recorded.

### Stage 1 — headless panel. **2–4 weekends.**

Pi Zero 2 W serving [12](12-panel-visual-design.md)'s design as a web page over its own Wi-Fi, one
unit, **read-only plus `STOP ALL`**. Phone as the screen. ~£30 of parts.

This is where the box becomes real, and it is deliberately read-only so that the whole of stage 2's
risk lands in one place.

### Stage 2 — the control set, and the guards. **Several weeks of evenings. This is the bulk.**

Everything in §4.2, with everything in §5 and §6 honoured. Be clear about the shape of this: **the
guards are more code than the UI.** The STOP verification state machine, the assist watchdog, the
preload detection, the error-vocabulary mapping, the flash-write batching — that is the project. The
buttons are the easy part, and they are already specified.

Add the screen here (£70–110 total), once there is something worth looking at.

### Stage 3 — multi-unit. **1–2 weekends.**

Discovery, assignment, and the re-enumeration discipline of §3.2. Genuinely short, because the
protocol already does the hard part. Add the A/B switch (option D) here if not sooner.

**Stages 0–3 total: realistically 40–80 hours over a couple of months.** That is a real personal
project. It is not a weekend, and anyone told otherwise is being sold something.

### Stage 4 — the Klipper bridge (option C). **Roughly doubles the total, and can kill a print.**

Transparent USB-serial pass-through with request/response tracking, two-stream arbitration, and the
failure modes of §1.3. **This is a genuine embedded project**, it needs a test rig rather than a live
printer, and it should not be attempted until stages 1–3 have run reliably for months. It is also the
stage most likely to be got subtly wrong in a way that only shows up on layer 200.

The test rig already exists and is CC0: `printers-for-people/ACEResearch` ships an **ACE emulator**.
Arbitration can be developed against emulated units and a real Klipper instance with no filament,
no heater and nothing to grind. That is a large de-risking for free, and it should be the first
thing set up in this stage rather than an afterthought.

### Not on the plan

* **A custom PCB.** Stages 0–3 need no board at all — a Pi and an £8 dongle. A PCB is a separate
  hobby with its own iteration time, and it should follow a working box, never precede one.
* **An ESP32 port.** The right move if this ever becomes a thing people buy. The wrong move for
  getting to a working box, for the reasons in §2.1.
* **A network API for Klipper.** Ruled out in §1.3 on measured latency grounds, before someone
  builds it because it is easier to write.

---

## 9. Open questions

Stated as questions, because none of them has been tested and guessing would undo the point of this
repository.

1. **Does an assigned unit still answer `DISCOVER` at address 0?** Decides whether option D scales
   past one unit. Fifteen minutes on a bench. (§1.4)
2. **What is the ACE 2 Pro's own RS-485 connector pinout, and does the port carry power?** Not
   documented anywhere here. (§2.3)
3. **Is the discovery back-off unit a character time?** Inferred, not confirmed. It sets how many
   unassigned units can be enumerated at once. (§3.3)
4. **Does `SET_PRINTER_STATUS = 0` have side effects beyond the documented preload cap and the RFID
   re-read suppression?** The exhaustive enumeration in [07 §3](07-subsystems-and-safety.md) says no,
   but that was read for a different purpose. It is being leaned on hard here. (§5.2)
5. **Can a standalone box detect patched firmware reliably?** UID passthrough reports
   `version 0x0201`, but that is a side effect rather than a version flag, and it only shows on a tag
   the ACE cannot decode. (§4.2)
6. **What address does Kobra-S1/ACEPRO actually use, and does it enumerate?** It decides whether the
   §1.4 switch is genuinely zero-configuration, and it sets the convention any new implementation
   should match. Answerable by reading the driver, not by experiment. (§1.1, §3.3)
7. **Will hakimio put a licence on `ace2_protocol.py`?** Worth asking, for the whole ecosystem rather
   than for this box. (§7.4)
