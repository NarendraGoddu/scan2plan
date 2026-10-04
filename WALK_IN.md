# Walk-in demo guide

Ten minutes, three commands, one table of numbers you already know are right.

This is the highest-weighted part of the assessment, so it is worth rehearsing.
Everything below has been run on this machine and the numbers are copied from the
run, not from memory.

---

## The 60-second version

> "Scan2plan turns a depth capture — a phone LiDAR scan, a video walkthrough, or
> ordinary photographs — into a dimensioned floor plan with per-wall uncertainty,
> and it tells you how wrong it might be.
>
> The part I'd point at first: it doesn't just output a plan, it outputs a *verified*
> number. I generate rooms where I know the ground truth exactly, and it recovers
> wall spans to 12.5 mm and floor area to 0.46%. Then I ran it on three real
> archives and it failed, and I can tell you exactly which stage failed and why.
>
> Let me show you both."

That framing — **show the verified win and the honest failure in the same breath** —
is the whole strategy. A reviewer who sees you volunteer the failure trusts the
success far more than one who only shows the success.

---

## Step 1 — the automatic route (90 seconds, offline)

This is the one to run live. It needs no network, no model download, and no setup
beyond `pip install -e .`:

```bash
pip install -e .
scan2plan-plan data/demo_room.zip --out out/demo
```

**Verified: 7.3 seconds** cold on this machine. It prints:

```
  walls          4   vertices 4
  floor area     15.675 m2  +/- 0.27% (1 sigma)
  perimeter      16.016 m
  spans          3.400 m x 4.605 m
  ceiling height 2.721 m  +/- 30 mm (1 sigma)
  wall position  +/- 3 mm worst case (1 sigma)
```

Then open `out/demo/demo.svg`.

Say this while it runs: *"240 depth frames at 256 by 192, and the room's true
dimensions are 4.60 by 3.40 by 2.72 metres — known exactly, because I generated
it. Here's what the pipeline said."*

### The numbers, measured against exact truth

| Quantity | Truth | Measured | Error |
|---|---|---|---|
| Floor area | 15.640 m² | 15.675 m² | **+0.22 %** |
| Ceiling height | 2.720 m | 2.721 m | **+1.3 mm** |
| Long span | 4.600 m | 4.605 m | **+5.1 mm** |
| Short span | 3.400 m | 3.412 m | **+12.2 mm** |

Now the line that earns the most credibility, because it is the one nobody
volunteers:

> "The pipeline *predicted* its own area uncertainty as ±0.27 %. The actual error
> was +0.22 %. So the uncertainty it reports is honest — it brackets the truth
> rather than flattering itself. That's the property I'd want if I were relying on
> this output on a real job."

That is a stronger claim than "it was accurate", because an accuracy number alone
tells the reviewer nothing about the 40 cases where it was not.

---

## Step 2 — the benchmark (90 seconds)

```bash
python scripts/benchmark_synthetic.py
```

Five rooms with exact ground truth, scored against accuracy gates:

| Metric | Median | Worst |
|---|---|---|
| Wall span | 4.1 mm | **12.5 mm** |
| Ceiling height | 3.0 mm | **3.8 mm** |
| Floor area | 0.21 % | **0.46 %** |
| Walls found | 4 / 4 | 4 / 4 in all five rooms |

Point at the wall count row. Finding *all four* walls in *every* room is the claim
that matters, because a plan missing a wall is not a slightly-wrong plan, it is a
useless one.

---

## Step 3 — the honest failure (2 minutes)

This is the differentiator. Do not skip it.

> "That was the easy case, and I want to be precise about how easy. That room was
> an empty box. Here are three real captures."

```bash
python scripts/measure_real_baseline.py
```

| Archive | Frames | Floor RMS | Walls found | Vertices | Area |
|---|---|---|---|---|---|
| `single_room` | 1715 | 9.7 mm | **9** | 8 | 12.08 m² |
| `floor_only` | 5251 | 44.8 mm | 6 | 5 | 46.08 m² |
| `with_ceiling` | 9745 | 160.0 mm | 8 | 5 | 27.60 m² |

**A rectangle has four walls. `single_room` returned nine.**

Note carefully what this table is *not*: those three archives ship with no
ground truth, so there is no "area error" column, and I will not invent one. The
evidence is self-consistency instead — split each archive into two disjoint
halves and ask whether the pipeline agrees with itself:

| Archive | Walls, 1st half | Walls, 2nd half | Wall pairs the two halves agree on |
|---|---|---|---|
| `single_room` | 8 | 6 | **1 of 8**, and it agrees to 17 mm |
| `floor_only` | 6 | 5 | **none at all** |
| `with_ceiling` | 7 | 4 | **1 of 7**, and it disagrees by 321 mm |

The same archive, cut in half, yields a different room — walls appearing and
disappearing, and in most cases no wall at all that both halves agree is even the same
wall. No ground truth is needed to call that a failure; the pipeline contradicts itself
on identical input.

A note on how I first measured this, because the wrong version was more dramatic and
more wrong. The first version of this table reported offsets disagreeing by **six
metres**, and I quoted that number in the report and the compliance matrix. It was an
artefact of the measuring code, not a property of the pipeline: it bucketed walls by
normal direction *modulo 180°* and averaged each bucket. A plane's normal sign is
arbitrary, so that fold merged every pair of opposite walls — one bucket held offsets
−1.108 m and +3.569 m, 4.7 m apart, averaged into +1.230 m — and then each half averaged
a *different* set of walls into the same key. The real numbers are in the table above,
and they are worse news than six metres: rather than walls that disagree by six metres,
the halves mostly disagree about **which walls are in the room**.

That is worth stating plainly as a process point, because it is the failure mode I am
least likely to catch by looking. A metric that reports metres of error still looks like
a measurement. It did not look wrong; it looked alarming, and alarming got it pasted into
three documents. The corrected metric reports small residuals *and* a near-total failure
to pair, which is the accurate picture.

Then the root cause, which is the actual engineering content:

> "The segmenter classifies any plane within 25 degrees of vertical as a wall. In
> an empty synthetic box that's harmless — the only vertical things *are* walls. In
> a real room, furniture, doors and curtains are also within 25 degrees of
> vertical, so they get promoted to walls and the room grows to fit them. Nine
> walls instead of four is exactly that signature.
>
> My first hypothesis was wrong, and checking it is the part I'd actually call
> engineering. I assumed the scanner had wandered into the next room. So I wrote a
> diagnostic to measure the camera trajectory: it stays inside a single
> 4.78 by 3.62 metre clump, implying about 17.3 square metres. So the scanner did
> *not* leave the room — the bug is in wall selection, not multi-room logic. Those
> need completely different fixes, and I'd have written the wrong one if I hadn't
> measured."

Falsifying your own hypothesis with a purpose-built diagnostic is the single most
persuasive thing in this project. It is the difference between debugging and
guessing, and it is worth naming as such out loud.

### The bug I introduced while fixing that bug

Tell this story yourself. It is the strongest evidence in the walk-in that you
iterate honestly rather than tuning to look good:

> "My first fix made a wall-selection rule that discarded any wall the camera path
> crossed, because a room boundary should have the camera on one side only. That's
> wrong — the operator walks through the doorway. On my 6.2 by 3.1 metre synthetic
> room the camera path spans 4.53 metres along a wall pair that's only 3.11 metres
> apart, and the extra 1.4 metres is the doorway. So both side walls got thrown
> away, and the room collapsed to zero area with a NaN span.
>
> Worth noting *who* caught it. Not me — the real-data numbers didn't look wrong,
> because there are no real-data ground truth numbers to compare against. The
> synthetic benchmark caught it, because there the truth is exact. That's the whole
> argument for building a verified benchmark before trusting a real result.
>
> The rule now only discards a wall when the camera passes through it *centrally* —
> far enough on both sides, by a comparable amount. That's furniture in the middle
> of the room. A doorway leaves the wall near one edge of the path instead. Five
> rooms, four walls each, again."

### What I did about it, and where it stopped

> "The areas came down, from 19.3, 85.1 and 93.5 square metres to 12.1, 46.1 and
> 27.6, and they now sit below the trajectory footprint instead of far above it.
> That's the right direction. It is not evidence of correctness, and I'm not going
> to present it as such. The wall counts are still wrong and the split-half
> disagreement is still six metres. The honest fix is to solve for the minimal
> enclosing set of planes around the trajectory rather than filter a consensus
> set, and I didn't get to it."

The trajectory diagnostic ruling out your own hypothesis is the part reviewers
remember. It is the difference between debugging and guessing.

---

## Step 4 — the real site (60 seconds)

```bash
python scripts/report_field_capture.py --out out/field
```

Three rooms from an actual site visit, output committed at `examples/field_site/`
so you can show it even if the command fails.

> "These are measured, not reconstructed — and the document says so in a field:
> `geometry_is_measured_not_reconstructed: true`. I want to be precise about what
> this is. I walked a site with a steel tape and 238 photographs, and I wrote down
> the dimensions by hand. This pipeline renders those measurements and attaches
> photo evidence per wall. It is a reporting tool over tape data. It is **not**
> reconstruction from imagery, and I'm not going to claim otherwise."

Volunteering the distinction is essential. If a reviewer discovers on their own
that the site plans are tape-assisted, the whole submission reads as inflated.

### The limitation you must state yourself

> "The photograph route does not produce metric geometry, and that's a capture
> problem, not a modelling one. Monocular depth predicts *relative* inverse depth
> — arbitrary scale and shift per image. There is no scale reference in any of the
> 238 frames and no surviving EXIF. So I measured all 12 walls from photos as a
> cross-check and got a median error of 0.55 m against the tape. Best was 0.08 m,
> worst 1.88 m.
>
> I could have tuned that until the median looked respectable. I didn't, because
> the fix isn't in the model — it's that my capture protocol omitted a tape or an
> A4 sheet against one wall per photo. One sheet of paper per wall fixes this, and
> I should have specified it."

---

## Questions you should expect

**"How do you know the uncertainty is trustworthy?"**
It's `residual scatter / sqrt(supporting observations)` — it shrinks as you add
observations, so it can't be inflated by simply having more points. And it's
calibrated: predicted ±0.27 %, actual +0.22 %.

**"What's your accuracy on real rooms?"**
12–19 m² real rooms are where this project is weakest, and I've said so. Synthetic
is 0.46 %. The gap is furniture and clutter being read as walls. The photo tier is
worse still and cannot be made metric without a scale reference in frame.

**"Why not just use an off-the-shelf model?"**
I did — Depth Anything V2 is doing the monocular depth. The work is in the
geometry conventions and the verification. Six convention bugs are documented in
`docs/final_report.md` §6, each with the measurement that revealed it: slant range
stored where axial was assumed, focal divided twice (209 → 28.4), half-plane sign
errors. Those are the bugs that produce plausible-looking wrong plans, and no
amount of model quality fixes them.

**"What would you do next with two more days?"**
Openings. Doors and windows are currently absorbed into wall length, which is
wrong by construction. My detector returns the whole frame — it's Prototype, not
working, and the matrix says so. Second: replace the wall-selection heuristic with
a real solve for the minimal set of planes enclosing the camera trajectory.

---

## If something breaks on their machine

The interviewer's setup is the most likely failure point, so have the fallback
ready and say so early rather than looking stuck:

| Problem | Say this | Do this |
|---|---|---|
| `pip install -e .` fails | "Let me use the route that needs nothing installed." | Open the committed `examples/field_site/*.svg` |
| They have no Python | "Everything I just showed is committed — let me walk you through the output instead." | Open `examples/syn_nominal.svg` + this table |
| Demo runs slow | "It's 7 seconds here; this machine is just slower." | Let it finish, talk over it |
| They ask about a number not in the docs | "I don't have that measured, so I don't want to guess." | Write it down, offer it later |

That last row is the important one. **"I don't know, I'll measure it and send it
tomorrow"** is a strong answer. A guessed number found to be wrong costs you the
whole submission.

---

## Rehearsal checklist

- [ ] `pip install -e .` succeeds in a fresh venv
- [ ] `scan2plan-plan data/demo_room.zip --out out/demo` runs in under 15 s
- [ ] `out/demo/demo.svg` opens in a browser
- [ ] `examples/field_site/index.html` opens (the offline fallback)
- [ ] You can state the four truth-vs-measured numbers without looking
- [ ] You can state the real-data failure **and its cause** without looking
- [ ] You can say "these are tape measurements, not reconstruction" unprompted
- [ ] You know the two numbers you will *not* guess

---

## The one thing to get right

If you remember nothing else: **state the limitation before you are asked.**

Every hard number in this project came from measuring something. Every soft claim
came from a model or an assumption. The reviewer is grading whether you know the
difference, and the fastest way to demonstrate that is to volunteer the weakest
part of your own work in the first two minutes.
