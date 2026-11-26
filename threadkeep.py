#!/usr/bin/env python3
"""
threadkeep — compress a long, messy paste into a brief that keeps the THREAD,
not just the conclusions. Every claim carries a line anchor, and every quote is
extracted mechanically from those lines, so the model cannot invent evidence.

The original text is never deleted: it is stored on disk with an id, and any
slice can be pulled back with `threadkeep --show <id> --lines 120-180`.

    threadkeep --file notes.txt --ask "what did we decide and why?"
    threadkeep --show 3a1409a4ff --lines 120-180

MIT licence. No dependencies beyond the Python standard library.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request

HOME = os.path.expanduser("~")
STORE = os.path.join(HOME, ".threadkeep")
HERMES_CONFIG = os.path.join(HOME, "AppData", "Local", "hermes", "config.yaml")
HERMES_ENV = os.path.join(HOME, "AppData", "Local", "hermes", ".env")

SYS = """You are an archivist, not a summarizer. You receive a long source, numbered by line ("12| text").
Build a BRIEF that preserves the THREAD of the discussion, not just its conclusions.

Answer with valid JSON only — no prose before or after, no ```:
{
 "fir": [{"pas":"what was proposed/decided/tried","motiv":"why, if the source says so","linii":"12-18"}],
 "fapte": ["exact numbers/names/paths/URLs, verbatim"],
 "lipsuri": ["what should be in the source and is not / what is ambiguous"]
}

Absolute rules:
- "fir" is chronological: from the origin to the end, including the FAILED attempts and why they failed.
- Each "pas" states WHAT was decided; "motiv" states WHY (leave empty if the source does not say).
- "linii" is MANDATORY: the line number (or range) that the entry comes from. Without it the entry is dropped.
- Do not quote text; the exact quote is extracted from your line numbers, not from you.
- Never invent. Never invent a motive. Never omit a failed test.
- "fir": at most 12 entries. "pas" max 18 words, "motiv" max 10 words, terse, no filler.
- "fapte": at most 15 entries, only the ones that matter for the request.
- "lipsuri": at most 5 entries, max 15 words each.
- Write every value in the SOURCE LANGUAGE stated in the request line, even if this prompt is in another language."""

DIRECTIVE = re.compile(
    r"\b(nu |nici|fără |fara |doar|numai|trebuie|obligatoriu|interzis|"
    r"cel puțin|cel putin|maxim|minim|niciodată|niciodata|"
    r"must|never|do not|don't|only|without|at least|no more than|exactly|required)\b",
    re.I,
)
CHAT = re.compile(
    r"\b(hai să|hai sa|încearc|incerc|văd|vad|se pare|probabil|poți|poti|"
    r"spune-mi|bun[ăa]|interesant|gata,|deci|let me|i'?ll|seems|probably|"
    r"maybe|okay|alright|let's)\b", re.I)


RO_MARK = re.compile(r"[șțăâî]|\b(si|sa|care|este|trebuie|pentru|din|nu)\b", re.I)


def detect_lang(src):
    sample = src[:4000]
    hits = len(RO_MARK.findall(sample))
    return "Romanian" if hits > 25 else "English"


def _read(path):
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError as e:
        sys.exit(f"cannot read {path}: {e.strerror}")


def tok(s):
    return max(1, round(len(s) / 3.5))


def _env_file(path):
    out = {}
    try:
        for line in open(path, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("'\"")
    except OSError:
        pass
    return out


def provider(args):
    base = args.base_url or os.environ.get("PF3_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    model = args.model or os.environ.get("PF3_MODEL")
    env = _env_file(HERMES_ENV)
    if not base:
        try:
            m = re.search(r"base_url:\s*(\S+)", open(HERMES_CONFIG, encoding="utf-8").read())
            base = m.group(1) if m else None
        except OSError:
            pass
    if not model:
        try:
            m = re.search(r"default:\s*([\w.\-:]+)", open(HERMES_CONFIG, encoding="utf-8").read())
            model = m.group(1) if m else None
        except OSError:
            pass
    key = (os.environ.get("PF3_API_KEY") or os.environ.get("OPENAI_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
           or env.get("PF3_API_KEY") or env.get("OPENAI_API_KEY") or env.get("DEEPSEEK_API_KEY"))
    if not key:
        sys.exit("no API key: set PF3_API_KEY (or OPENAI_API_KEY / DEEPSEEK_API_KEY)")
    if not model:
        sys.exit("no model: pass --model or set PF3_MODEL")
    return (base or "https://api.openai.com/v1").rstrip("/"), key, model


def call(messages, args, max_tokens=8000, temperature=0):
    base, key, model = provider(args)
    body = {"model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens}
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    ch = d["choices"][0]
    return ch["message"]["content"], d.get("usage", {}), ch.get("finish_reason")


def parse_json(txt):
    txt = (txt or "").strip()
    txt = re.sub(r"^```(?:json)?|```$", "", txt, flags=re.M).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no JSON in response")
    return json.loads(txt[i:j + 1])


def directives(lines):
    """Sentences that carry a requirement or constraint — regex, not AI, so verbatim."""
    out = []
    for n, line in enumerate(lines, 1):
        s = line.strip()
        if len(s) < 12 or len(s) > 300:
            continue
        is_asst = s.lower().startswith(("[assistant]", "assistant:"))
        if is_asst and CHAT.search(s):
            continue
        if DIRECTIVE.search(s):
            out.append((n, re.sub(r"^(\[user\]|\[assistant\]|user:|assistant:)\s*", "", s)))
    return out[:12]


def build(src, ask, args):
    lines = src.splitlines()
    rid = hashlib.sha256(src.encode()).hexdigest()[:10]
    os.makedirs(STORE, exist_ok=True)
    raw_path = os.path.join(STORE, rid + ".txt")
    open(raw_path, "w", encoding="utf-8").write(src)

    numbered = "\n".join(f"{i}| {l}" for i, l in enumerate(lines, 1))
    user = (f"SOURCE LANGUAGE: {detect_lang(src)}\n"
            f"REQUEST: {ask or '(unspecified — keep everything about decisions and tests)'}\n\n"
            f"SOURCE ({len(lines)} lines):\n{numbered}")
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": user}]
    content, usage, finish = call(msgs, args)
    open(os.path.join(STORE, rid + ".response.txt"), "w", encoding="utf-8").write(content or "")
    data = None
    try:
        data = parse_json(content)
    except Exception as e:
        content2, usage2, finish2 = call(
            msgs + [{"role": "assistant", "content": (content or "")[:400]},
                    {"role": "user", "content": "That was not valid JSON. Answer again: JSON only, "
                                                "first character {, no comments."}], args)
        open(os.path.join(STORE, rid + ".response2.txt"), "w", encoding="utf-8").write(content2 or "")
        usage = {"prompt_tokens": usage.get("prompt_tokens", 0) + usage2.get("prompt_tokens", 0),
                 "completion_tokens": usage.get("completion_tokens", 0) + usage2.get("completion_tokens", 0)}
        finish = finish2
        try:
            data = parse_json(content2)
        except Exception:
            data = {"fir": [], "fapte": [],
                    "lipsuri": [f"invalid JSON ({e}) — see {rid}.response.txt"]}
    if finish == "length" or not data.get("fir"):
        content3, usage3, finish3 = call(
            msgs + [{"role": "user", "content": "Answer again, terse, JSON only — first character {"}],
            args, max_tokens=16000)
        open(os.path.join(STORE, rid + ".response3.txt"), "w", encoding="utf-8").write(content3 or "")
        try:
            d3 = parse_json(content3)
            if d3.get("fir"):
                data, finish = d3, finish3
                usage = {"prompt_tokens": usage.get("prompt_tokens", 0) + usage3.get("prompt_tokens", 0),
                         "completion_tokens": usage3.get("completion_tokens", 0)}
        except Exception:
            pass

    # ── mechanical verification: quotes come from the source, not from the model ──
    warns, fir = [], []
    for v in data.get("fir", [])[:12]:
        pas = str(v.get("pas", "")).strip()
        ln = str(v.get("linii", "")).strip()
        m = re.search(r"(\d+)\s*(?:[-–]\s*(\d+))?", ln)
        dov, ok = "", False
        if m:
            a = int(m.group(1))
            b = min(int(m.group(2) or a), len(lines))
            if 1 <= a <= b <= len(lines):
                dov = re.sub(r"\s+", " ", " ".join(lines[a - 1:b])).strip()[:110]
                ok = True
        if not ok:
            warns.append(f"entry without a valid anchor: “{pas[:50]}”")
        fir.append({"pas": pas, "motiv": str(v.get("motiv", "")).strip(),
                    "dovada": dov, "linii": f"{a}-{b}" if ok else "", "ok": ok})

    NUM = re.compile(r"\d+(?:[.,]\d+)*")
    src_nums = set(NUM.findall(src))
    fapte = [str(x).strip() for x in data.get("fapte", [])][:15]
    bad = [f for f in fapte if NUM.search(f) and not set(NUM.findall(f)) <= src_nums]
    fapte_ok = [f for f in fapte if f not in bad]
    if bad:
        warns.append(f"{len(bad)} fact(s) with numbers absent from the source (removed)")

    return {"id": rid, "raw": raw_path, "lines": len(lines), "usage": usage,
            "finish": finish, "fir": fir, "fapte": fapte_ok, "bad_fapte": bad,
            "lipsuri": [str(x) for x in data.get("lipsuri", [])],
            "constrangeri": directives(lines), "warns": warns, "src_tok": tok(src)}


def render(b, ask):
    L = [f"# BRIEF {b['id']}\n"]
    L.append("## 1. REQUEST (verbatim, never compressed)\n")
    L.append((ask.strip() if ask and ask.strip()
              else "(unspecified — the brief keeps the whole thread)") + "\n")
    L.append("## 2. CONSTRAINTS AND REQUIREMENTS (verbatim from the source)\n")
    if b["constrangeri"]:
        for n, s in b["constrangeri"]:
            L.append(f"- (L{n}) {s}")
    else:
        L.append("- (none detected)")
    L.append("\n## 3. THE THREAD (chronological — including what failed)\n")
    for i, v in enumerate(b["fir"], 1):
        anc = f" [L{v['linii']}]" if v["linii"] else " [UNVERIFIED]"
        L.append(f"{i}. {v['pas']}")
        if v["motiv"]:
            L.append(f"   - why: {v['motiv']}")
        L.append(f"   - evidence: “{v['dovada']}”{anc}" if v["dovada"]
                 else f"   - evidence: —{anc}")
    if not b["fir"]:
        L.append("(thread not extracted — see the source)")
    L.append("\n## 4. FACTS, NUMBERS, NAMES\n")
    for f in b["fapte"]:
        L.append(f"- {f}")
    if not b["fapte"]:
        L.append("- (none)")
    if b["lipsuri"]:
        L.append("\n## 5. MISSING OR AMBIGUOUS\n")
        for x in b["lipsuri"]:
            L.append(f"- {x}")
    L.append("\n## 6. FULL SOURCE — nothing was lost\n")
    L.append(f"- id: `{b['id']}` · {b['lines']} lines · ~{b['src_tok']} tok · `{b['raw']}`")
    L.append(f"- any slice, on demand: `threadkeep --show {b['id']} --lines 120-180`")
    return "\n".join(L)


def show(rid, rng):
    p = os.path.join(STORE, rid + ".txt")
    if not os.path.exists(p):
        sys.exit(f"no such id: {p}")
    lines = open(p, encoding="utf-8").read().splitlines()
    m = re.match(r"(\d+)\s*[-–]\s*(\d+)", rng or "")
    a, b = (int(m.group(1)), int(m.group(2))) if m else (1, len(lines))
    for i in range(a, min(b, len(lines)) + 1):
        print(f"{i}| {lines[i-1]}")


def main():
    ap = argparse.ArgumentParser(description="Keep the thread, drop the bulk.")
    ap.add_argument("--file", help="source text (or - for stdin)")
    ap.add_argument("--ask", default="", help="your request, kept verbatim")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--out", help="where to write the brief (.md)")
    ap.add_argument("--show", help="id of a stored source")
    ap.add_argument("--lines", help="line range for --show, e.g. 120-180")
    ap.add_argument("--quiet", action="store_true", help="print only stats")
    ap.add_argument("--min-chars", type=int, default=6000,
                    help="do not compress sources shorter than this (default 6000)")
    a = ap.parse_args()

    if a.show:
        show(a.show, a.lines)
        return
    if not a.file:
        ap.error("pass --file or --show <id>")
    src = (sys.stdin.read() if a.file == "-"
           else _read(a.file)).strip()
    if not src:
        sys.exit("empty source")

    if len(src) < a.min_chars:
        print(f"source is {len(src)} chars (~{tok(src)} tok) — below the {a.min_chars}-char "
              f"threshold, compression would cost more than it saves. Pass it through unchanged.")
        return

    b = build(src, a.ask, a)
    md = render(b, a.ask)
    out = a.out or os.path.join(STORE, b["id"] + ".brief.md")
    open(out, "w", encoding="utf-8").write(md)

    if not a.quiet:
        print(md)
    print("\n--- STATS ---", file=sys.stderr if a.quiet else sys.stdout)
    print(f"source : {len(src):7d} chars ~{b['src_tok']:7d} tok ({b['lines']} lines)")
    print(f"brief  : {len(md):7d} chars ~{tok(md):7d} tok ({100*len(md)/len(src):.1f}% of source)")
    print(f"entries: {len(b['fir'])} ({sum(1 for v in b['fir'] if v['ok'])} with verified anchor)")
    print(f"facts  : {len(b['fapte'])} verified"
          + (f", {len(b['bad_fapte'])} removed as invented" if b['bad_fapte'] else ""))
    print(f"api    : in={b['usage'].get('prompt_tokens','?')} "
          f"out={b['usage'].get('completion_tokens','?')} finish={b['finish']}")
    for w in b["warns"]:
        print("warning:", w)
    print(f"brief written: {out}")
    if b["finish"] == "length":
        print("!! response truncated — brief incomplete (lower the source size)")
        sys.exit(1)


if __name__ == "__main__":
    main()
