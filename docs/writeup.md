# What it costs to stop a local model from making things up

A 9.7B model running on a 12GB consumer card produced compliance case files
that passed seven verification checks on the first attempt, every time, across
sixteen cases. A 3B model on the same hardware, same evidence, same checks,
managed 12%.

That gap is the result I set out to find. Everything below is how I measured it
and what else fell out along the way.

## The system

`casefile` is a self-hosted pipeline that investigates a blockchain address and
writes up what it found. Four stages:

- **ingest** pulls transactions and transfers for an address into a local
  database, behind a chain-agnostic interface.
- **enrich** labels counterparties from three provenance tiers, then traces
  value flows across hops.
- **gate** applies deterministic rules to produce a verdict with reasons and
  evidence. No model runs here.
- **narrate** hands the verdict to a local language model, which writes the case
  file. Seven checks verify the output before anyone reads it.

The split is the point. Rules decide, the model narrates. The model never
weighs, never concludes, and never contributes a fact that the rules did not
already establish.

## Why seven checks and not a prompt

You cannot instruct a model into honesty, and you cannot check it by reading the
output. So the checks are structural.

Four of them are exact and reject the narrative outright: every transaction hash
in the prose must exist in the evidence; every address must exist in the
evidence; the verdict stated must be the verdict the gate reached; and where
confidence is low, the narrative must say so. A fifth is heuristic and flags for
review rather than rejecting, because it needs to judge whether a sentence is
making a claim at all, and that is not a solved problem.

Two more came later, and both came from measuring rather than designing.

Citation precision was 100% from the first run. Finding coverage was around 50%.
The model was citing flawlessly and silently omitting half of what the rules had
found. A case file that cites perfectly and drops half the findings is worse
than one that fails loudly, because it looks trustworthy. That produced check 6:
the narrative carries a structured line naming every rule that fired, and the
check compares that set against the verdict exactly.

Then check 7, because naming a finding is not the same as substantiating it. For
every rule named, at least one of that rule's own evidence items must appear in
the prose.

The general lesson is that anything enumerable belongs in the exact tier.
Twice I wrote a check that parsed prose for something the data already knew, and
twice it was wrong: the verdict check flagged a narrative for using the word
"unassessable" as an ordinary adjective. Replaced with a mandated header line
and an exact match, it has not misfired since.

## Two things the system caught in itself

**Six verdicts were wrong and nobody knew.** A rule change earlier in the
project left older stored verdicts carrying an obsolete rule whose precedence
masked real findings. Six cases stored as "unassessable" recompute to "flagged"
from unchanged evidence. The findings had always been in the rule list. The
verdict logic had been hiding them.

This surfaced because `gate replay` exists: every verdict can be recomputed from
its stored case and must match. It failed loudly on six rows. Without it, six
wrong answers would have propagated into every downstream measurement.

**A fabricated transcript nearly reached the README.** During documentation, an
output block was reconstructed rather than captured. The content was almost
certainly identical to what the command would have printed. It was still
invented, in a document whose entire claim is that every number traces to a real
run. It was caught, replaced with genuine captured output, and the project's
working rules now prohibit any output in documentation that was not captured
from an actual run, including when it would be the same anyway.

Both incidents are in the repository with the real diffs. They are more
persuasive than any argument about good practice, because they are things that
went wrong and got caught by the design rather than by luck.

## The measurements

Sixteen stored verdicts, four configurations, on an RTX 5070 with 12GB.

| Configuration | First-attempt pass | Fallback | Citation precision |
|---|---|---|---|
| `qwen3.5:9b` Q4_K_M | 100% | 0% | 100% |
| `qwen3.5:9b` Q8_0 | 100% | 0% | 100% |
| `qwen2.5:3b` Q4_K_M | 12% | 88% | 100% |
| `qwen3.5:9b` Q4_K_M, thinking on | 0% | 100% | 100% |

Four things worth extracting.

**The capability threshold sits between 3B and 9B for this task.** Requiring
substantiation cost the 3B model almost everything: first-attempt pass fell from
69% to 12%. Both 9B configurations absorbed the same requirement at zero cost.
Quantisation barely mattered; parameter count did.

**Thinking mode never finishes.** On a 2,838-token evidence bundle, with the
budget pushed to 6,000 tokens, the model spent the entire budget on its reasoning
trace and emitted zero narrative characters, taking up to 76 seconds. The trace
itself reproduced byte for byte, so this is non-termination, not
non-determinism. The deliberation already happened in the rules; asking the model
to deliberate again produces nothing and costs everything.

**Instruction-following capacity is a budget you spend.** I added two prompt
constraints aimed at an advisory check that never rejects anything. The Q8_0
row's first-attempt pass rate fell from 100% to 75%, and the Q4 row's from 100%
to 94%, on checks neither instruction touched. Two sentences of guidance cost
real ground elsewhere. Both were reverted, and the intent moved into
verification, where it costs nothing.

**Q8_0 fits a 12GB card, with a measurable ceiling.** It holds the largest real
bundle up to 8192 context with 330MB free, and fails outright at 12288. Not a
yes or a no, a number.

## What I do not know

The uncited-assertion check is heuristic. I hand-adjudicated one full sweep:
24 flagged sentences, 24 false positives, zero fabrications. That produced three
exemptions, but the false-positive rate is not measured automatically and I have
not adjudicated a second sweep. The current sweep flags 21 sentences.

Reproducibility does not survive a model reload. Two calls in one loaded session
with the same seed are byte-identical. Reload the model in between and the prose
differs, though every cited hash and address was real both times. Probably GPU
kernel selection varying per load. I did not chase it further, so the honest
claim is "reproducible within a session", not "reproducible".

Label coverage is thin. Most counterparties in the dataset are unlabelled, which
means the rule that stops tracing at custody changes fires rarely. The system
reports that as low confidence rather than hiding it, but low confidence is what
most real output currently carries.

Timing language drifts. Without a prompt constraint, 21% of narratives describe a
100-block pass-through window, roughly twenty minutes, as "immediately". That is
now flagged for review rather than corrected, because correcting it in the prompt
cost more than it fixed.

## The part I would argue for

Most of this pipeline is ordinary engineering. The part worth copying is the
refusal to let absence of evidence read as evidence of absence.

When tracing stops, it records why: hop limit reached, custody changed at an
exchange, fan-out exceeded, or the next node was never ingested. That last one is
a statement about the database, not the blockchain, and collapsing it into the
others would let "we did not look" read as "there is nothing there".

The same distinction runs all the way up. A rule that cannot be evaluated returns
unknown, never a default pass. A verdict with no findings and incomplete coverage
is "unassessable", never "clear". Clear with low confidence is impossible by
construction.

Findings are treated asymmetrically, and deliberately. A gap in coverage
threatens a negative conclusion, not a positive one: finding a transfer to a
sanctioned address is a fact that an un-ingested node elsewhere does not unfind.
So evidence found flags regardless of gaps, and only the absence of evidence has
to prove its own completeness. It took three attempts to get that right, and the
first two versions produced a system that could only ever say "I do not know".

Code, full numbers and both incident write-ups:
github.com/dpemmanouilidis/casefile
