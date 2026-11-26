# threadkeep

**Paste 50k tokens of messy notes. Get back a brief where every claim points at a line of the original — and the original is still on disk, so nothing is lost.**

A summary throws the thread away: you keep the conclusions, you lose where the problem started, why a decision was made, which tests failed, and what was tried and abandoned. threadkeep keeps the thread. It is an archivist, not a summarizer.

```bash
pip install nothing            # standard library only, single file
export PF3_API_KEY=sk-...      # any OpenAI-compatible endpoint
export PF3_BASE_URL=https://api.deepseek.com
export PF3_MODEL=deepseek-chat

python threadkeep.py --file notes.md --ask "where did this start, what did we decide and why, what failed?"
```

Output: a six-layer brief, plus the source stored under `~/.threadkeep/<id>.txt` where you can pull any slice back on demand.

---

## Why not just summarize?

Three things die in a summary, and all three are what you actually need two weeks later:

1. **The origin** — *"the request was to fix latency"* does not tell you the request was *"p95 went from 180ms to 2.4s after the 2026-02-11 release and tickets doubled"*.
2. **The reason** — *"decided to re-enable pooling"* does not tell you it was because a single AZ was slow and `max_open=40` had to be kept after a previous incident.
3. **The failures** — *"tested v3 lookup"* hides the fact that it was never load tested and the canary is stuck at 1%.

A summarizer also **invents citations**. That is not a moral failing, it is mechanical: models paraphrase, and a paraphrase of a quote is a fabricated quote.

threadkeep fixes that by never letting the model write the quote.

## The trick: the model gives line numbers, Python extracts the text

The model returns only `{"pas", "motiv", "linii"}`. The `linii` field is a line range, and it is **mandatory**. Python then slices those lines out of the source and puts the exact text in the brief, wrapped in quotes. A wrong range produces a visibly wrong quote. There is no way to fabricate evidence, because the model never types the evidence.

Measured on the same 913-line chat log:

| Prompt style | Entries with a valid, faithful quote |
|---|---|
| model writes the quote | **6 / 19** (13 paraphrased or invented) |
| model writes the line range, Python extracts | **16 / 16** |

Same goes for numbers: any "fact" whose digits do not appear in the source is dropped, not corrected.

## The six layers

| # | Layer | How it is produced |
|---|---|---|
| 1 | **REQUEST** | Your `--ask`, pasted verbatim. Never compressed. |
| 2 | **CONSTRAINTS** | Sentences matching *must / never / only / without / doar / fără / trebuie / cel puțin …* — extracted by regex, so verbatim, not by the model. |
| 3 | **THREAD** | Chronological: decided / tried / why / evidence, including dead ends and failed tests. |
| 4 | **FACTS** | Numbers, names, paths, URLs — each checked against the source's digit set. |
| 5 | **MISSING** | What the source does not settle. The tool is allowed to say "unknown". |
| 6 | **FULL SOURCE** | id, line count, path on disk, and the exact command to get any slice back. |

Layer 1 is the answer to *"but what if it deletes half of what I wrote and the model no longer understands what I want?"* — your request is not summarized at all, it is quoted.

## Real numbers (not estimates)

**Run A — a real 913-line conversation dump, heavy redundancy and repetition:**

```
source : 193,621 chars ~55,320 tok (913 lines)
brief  :  10,621 chars ~ 3,035 tok   →  5.5% of the source
entries: 16 (16 with verified anchor)
facts  : 16 verified, 0 invented
api    : in=73,161 out=4,238  finish=stop   (~20 s, ~2¢)
```

**Run B — a dense 144-line incident log (the one in `demo/`):**

```
source : 8,523 chars ~2,435 tok (144 lines)
brief  : 6,684 chars ~1,910 tok   →  78% of the source
entries: 12 (12 with verified anchor)
facts  : 15 verified
```

Read Run B honestly: **on already-dense text there is almost nothing to win.** Every line there is signal, so a thread-preserving brief cannot be much smaller than the source. The value in that case is the structure and the anchors, not the size.

Compression comes from redundancy — chat logs, pasted stack traces, retried attempts, the same statement explained four times. That is where 5–20× happens. Below `--min-chars 6000` the tool refuses to run and tells you to pass the text through unchanged, because the call would cost more than it saves.

## Usage

```
threadkeep --file notes.md --ask "what did we decide and why?"   # write the brief
threadkeep --file - --ask "..."  < paste.txt                     # from stdin
threadkeep --show 3a1409a4ff --lines 120-180                      # pull back the original
threadkeep --file notes.md --out brief.md --quiet                 # stats only
```

Exit code is `1` when the brief is incomplete (response truncated), `0` otherwise. A source the model cannot answer returns `MISSING:` lines rather than a guess.

### Configuration

| Env | Meaning | Default |
|---|---|---|
| `PF3_API_KEY` | API key (also `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`) | — |
| `PF3_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `PF3_MODEL` | model id | read from `~/.hermes/config.yaml` if present |

`--model` and `--base-url` override the env. Nothing is sent anywhere except the configured endpoint; the source also stays on your disk under `~/.threadkeep/`.

## What it does not do

- **It does not verify completeness.** If the model omits an entry, the brief will not warn you. The anchors prove nothing is *wrong*; the source on disk is what proves nothing is *lost*. That is why layer 6 exists.
- **The "why" is written by the model** — anchored to a line range, but phrased by it. It can be interpreted; the quote next to it is exact.
- **It is not a RAG index.** No embeddings, no vector store, no chunking. One pass, one brief, one pointer back to the raw text.
- **Not free.** One call per source, roughly 1× source tokens in and 5–10% out.

## When it is worth it

Pastes over ~15k tokens that you will refer back to: incident threads, long chats with an assistant, meeting transcripts, requirements hidden in email chains. Under that, just send the text.

## Licence

MIT. Single file, no dependencies. Name and file (`threadkeep.py`) are the whole tool.
