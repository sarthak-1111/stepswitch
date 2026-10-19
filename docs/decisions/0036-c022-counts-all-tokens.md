# ADR 0036: C-022 counts all tokens, and shows the uncached comparison beside it

- Status: accepted
- Date: 2026-10-20
- Phase: 11

## Context

C-022 was registered before the comparison runs (ADR 0034) as:

> "On the mini suites, Stepswitch uses fewer uncached tokens per task than Claude Code on
> Sonnet 5.5 and on Opus 5.5."

Its measure was uncached tokens (ADR 0028): input not served from a prompt cache, plus output.
The comparison ran on both mini suites with two seeds, and every contestant passed all 42
task runs. The claimed measure failed.

| Tokens over the 42 task runs | Stepswitch (router, local) | Claude Code, Sonnet 5.5 | Claude Code, Opus 5.5 |
|---|---:|---:|---:|
| All tokens (cached included) | 1,767,412 | 3,082,370 | 3,944,626 |
| Uncached tokens | 400,665 | 327,510 | 394,194 |
| Tokens sent to a hosted model | 0 | 3,082,370 | 3,944,626 |

With 95% intervals from a cluster bootstrap over tasks:

- **Against Sonnet 5.5**, Stepswitch processed 42.7% fewer tokens (28.7% to 55.3%) and used
  22.3% more uncached tokens (6.3% to 38.5% more).
- **Against Opus 5.5**, it processed 55.2% fewer tokens (42.2% to 66.3%) and used 1.6% more
  uncached tokens (20.0% fewer to 16.4% more), so no difference was measured.

Why the two measures disagree:

- Stepswitch's prompts are much shorter: it sends about half the tokens.
- The hosted service's prompt cache served about 91% of Claude Code's input. The local
  server's cache served 78% to 84% of Stepswitch's.
- So the hosted service had fewer tokens left to compute, even though Claude Code sent more.

## Decision

1. **The registered claim failed.** Its test is not changed to pass. The wording above stays
   on record here.
2. **C-022 is narrowed to what was measured:**

   > "On the mini suites, Stepswitch processes fewer tokens per task than Claude Code on
   > Sonnet 5.5 and on Opus 5.5, counting cached tokens, and sends none to a hosted model."

   - The measure becomes all tokens: input with cached tokens included, plus output. That is
     the measure C-011 started from before ADR 0028.
   - The `significant` threshold stays: the interval of the ratio must lie below one.
   - The test also checks that the router sent no token to a hosted model
     (`hosted_tokens_max: 0`), so it now asserts more than before, not less.
3. **The README shows the uncached comparison beside the claim.** The claim's README numbers
   include how many percent more uncached tokens Stepswitch used than each frontier model.
   The evidence keeps both measures with their intervals.

## Consequences

- C-022 no longer says anything about the compute or price of a hosted service. It says
  Stepswitch processes fewer tokens, and that all of them stay on the user's machine.
- A reader can see the result that went against Stepswitch next to the one that went for
  it.
- Making the router's prompts more cacheable on local servers is open work (PROGRESS).
