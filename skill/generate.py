#!/usr/bin/env python3
"""Ask a language model for new sketches, check each one, send the error back, keep what passes.

    python3 generate.py --endpoint http://localhost:8080/v1 --model NAME [-n 20] [--retries 3]

Works with any OpenAI-compatible chat endpoint (llama.cpp's llama-server, vLLM, Ollama's /v1,
LM Studio, hosted APIs). The API key, if one is needed, comes from $OPENAI_API_KEY (or --key-env).
Standard library only.

Each request gets the system prompt (prompt/system.md) with K examples drawn at random from
sketch-writer/examples/ (seeded, so a run can be repeated), and the user prompt (prompt/user.md)
with a subject (--subject / --subjects file) or a free choice. The answer's code block is checked
with the repository's checker; on a failure the model gets the error, the failing line quoted and
a hint (prompt/feedback.md), and writes the whole sketch again, up to --retries times.

Output in --out (default ./generated/<time>):
    pass/<slug>.pde, pass/<slug>.png   passing sketches and their 8-frame contact sheets
    attempts/<request>-<try>.pde       every answer as checked
    log.jsonl                          one line per request: tries, errors, tokens, seconds
    overview.png                       the last frame of every passing sketch, side by side
    summary.json                       pass rates and the most common failures
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import re
import shlex
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(HERE, "sketch-writer", "examples")
PROMPTS = os.path.join(HERE, "prompt")
DEFAULT_CHECKER = "immanence"            # the repository's command; falls back to python3 -m <it>
EX_START, EX_END = "<!-- examples:start -->", "<!-- examples:end -->"


# ---------------------------------------------------------------- prompts
def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_examples() -> dict[str, str]:
    return {n[:-4]: read(os.path.join(EXAMPLES, n)).strip()
            for n in sorted(os.listdir(EXAMPLES)) if n.endswith(".pde")}


def pick_examples(pool: dict[str, str], k: int, rnd: random.Random, budget: int) -> list[str]:
    """k examples at random, in random order, while their text fits `budget` characters."""
    names = sorted(pool)
    rnd.shuffle(names)
    out, size = [], 0
    for n in names:
        if len(out) >= k:
            break
        if size + len(pool[n]) <= budget:
            out.append(n)
            size += len(pool[n])
    return out


def system_prompt(template: str, examples: list[str]) -> str:
    """The template with its examples block replaced by these examples (none: left as written)."""
    if not examples or EX_START not in template:
        return template
    block = "\n\n".join("```processing\n" + e + "\n```" for e in examples)
    a, b = template.index(EX_START), template.index(EX_END) + len(EX_END)
    return template[:a] + block + template[b:]


def user_prompt(template: str, subject: str | None) -> str:
    s = f"Its subject: {subject}." if subject else "The subject is your own choice."
    return template.replace("{{SUBJECT}}", s)


# ---------------------------------------------------------------- the model
def chat(a, messages: list[dict], seed: int) -> dict:
    body = {"model": a.model, "messages": messages, "temperature": a.temperature, "top_p": a.top_p,
            "max_tokens": a.max_tokens, "seed": seed, "stream": False}
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(a.key_env)
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(a.endpoint.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=a.timeout) as r:
                d = json.loads(r.read())
            msg = d["choices"][0]["message"]
            return {"text": msg.get("content") or "", "finish": d["choices"][0].get("finish_reason"),
                    "usage": d.get("usage") or {}}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt == 2:
                raise
            print(f"  endpoint error ({e}); retrying", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:```|\Z)", re.S)
PIECE = re.compile(r'^\s*//\s*piece\s+"([^"]*)"', re.M)
WHY = re.compile(r'^\s*//\s*why\s+"([^"]*)"', re.M)


def extract(text: str) -> tuple[str | None, list[str]]:
    """The sketch in an answer, and notes on what was cleaned up. Takes the fenced block that holds
    a `// piece` line (else the longest fenced block, else the whole answer), drops text before the
    `// piece` line and prose after the last closing brace."""
    notes = []
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    blocks = [m.group(2) for m in FENCE.finditer(text)]
    if blocks:
        with_piece = [b for b in blocks if PIECE.search(b)]
        code = (with_piece or sorted(blocks, key=len, reverse=True))[0]
        if len(blocks) > 1:
            notes.append(f"{len(blocks)} code blocks; used one")
    else:
        code = text
    m = PIECE.search(code)
    if m:
        if code[:m.start()].strip():
            notes.append("text before // piece dropped")
        code = code[m.start():]
    elif not re.search(r"\b(void\s+(setup|draw)|size\s*\()", code):
        return None, notes + ["no sketch in the answer"]
    else:
        start = re.search(r"^\s*(//|/\*|void|int|float|boolean|String|color|final|class|[A-Z]\w*\s+\w+\s*[=;])", code, re.M)
        if start and code[:start.start()].strip():
            notes.append("text before the code dropped")
            code = code[start.start():]
        code = '// piece "untitled"\n// why ""\n' + code
        notes.append("no // piece line; added one")
    last = code.rfind("}")
    if last >= 0 and code[last + 1:].strip() and not code[last + 1:].lstrip().startswith("//"):
        notes.append("text after the last } dropped")
        code = code[:last + 1]
    return code.strip() + "\n", notes


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "untitled"


# ---------------------------------------------------------------- the check
class Checker:
    def __init__(self, cmd: str, vm: str | None, frames: int):
        self.cmd, self.vm, self.frames = shlex.split(cmd), vm, frames

    def _run(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(self.cmd + args, capture_output=True, text=True, timeout=timeout)

    def check(self, path: str, sheet: str | None = None, png: str | None = None, scale: int = 2) -> dict:
        args = ["check", path, "--frames", str(self.frames), "--json", "--scale", str(scale)]
        if self.vm:
            args += ["--vm", self.vm]
        if sheet:
            args += ["--sheet", sheet]
        if png:
            args += ["--png", png]
        try:
            p = self._run(args)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "error": {"kind": "deadline", "message": "the check timed out", "line": 0}}
        try:
            return json.loads(p.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise SystemExit(f"the checker did not answer ({' '.join(self.cmd)}): {p.stderr.strip()[-400:]}")

    def frame_grey(self, path: str, frame: int) -> bytes | None:
        """Frame `frame` (1-based) as 240 x 128 grey bytes, from the reference interpreter."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "frames.raw")
            try:
                self._run(["run", path, "--frames", str(max(1, frame)), "--raw", raw], timeout=120)
                data = read_bytes(raw)
            except (subprocess.TimeoutExpired, OSError):
                return None
        n = 240 * 128
        if len(data) < n:
            return None
        k = min(frame, len(data) // n) - 1
        return data[k * n:(k + 1) * n]

    def runtime_line(self, path: str, frames: int) -> tuple[int, str] | None:
        """A runtime fault on the VM carries no line: replay on the reference interpreter for one."""
        try:
            p = self._run(["run", path, "--frames", str(max(1, frames))], timeout=120)
        except subprocess.TimeoutExpired:
            return None
        m = re.search(r"^END fault \S+ (.*?)(?: \(line (\d+)\))?$", p.stdout, re.M)
        if m and m.group(2):
            return int(m.group(2)), m.group(1)
        return None


def resolve_checker(cmd: str | None) -> str:
    if cmd:
        return cmd
    from shutil import which
    return DEFAULT_CHECKER if which(DEFAULT_CHECKER) else f"{sys.executable} -m {DEFAULT_CHECKER}"


# ---------------------------------------------------------------- feedback a small model can act on
REFERENCES = [os.path.join(HERE, "sketch-writer", "reference", n) for n in ("language.md", "library.md")]
_INDEX: dict = {}


def ref_index() -> dict:
    """Every identifier the references mention, and for each function the first usage line that
    shows its arguments (from the reference text itself, so it follows the references)."""
    if not _INDEX:
        text = "\n".join(read(p) for p in REFERENCES if os.path.exists(p))
        _INDEX["names"] = sorted(set(re.findall(r"\b[A-Za-z_]\w*\b", text)) | {"frameCount", "width", "height"})
        sig = {}
        for m in re.finditer(r"\b([a-zA-Z]\w*)\(([^()`|]*)\)", text):
            if m.group(2).strip() and m.group(1) not in sig:
                sig[m.group(1)] = f"{m.group(1)}({m.group(2).strip()})"
        _INDEX["sig"] = sig
    return _INDEX


def did_you_mean(name: str) -> str:
    import difflib
    alts = {"frames": "frameCount", "frame": "frameCount", "w": "width", "h": "height", "PVector": "two floats"}
    if name in alts:
        return alts[name]
    m = difflib.get_close_matches(name, ref_index()["names"], n=1, cutoff=0.8)
    return m[0] if m and m[0] != name else ""


HINTS = [
    (r"ArrayList|IntList|FloatList|StringList|HashMap|\bList\b|\bMap\b",
     "lists and maps do not exist here. Use a fixed-size array made once at the top, and a count: "
     "float[] xs = new float[200]; int n = 0;"),
    (r"PVector", "PVector does not exist here. Use two floats (float x, y;) or two arrays (float[] px, py;)."),
    (r"PImage|PGraphics|createGraphics|createImage|loadImage|copy\(\)|image\(\)",
     "images and off-screen buffers do not exist here. Draw straight onto the canvas every frame. "
     "For trails, call fade(40) instead of background()."),
    (r"split|join\(\)", "split() and friends do not exist here. Write the words as an array: String[] w = {\"a\", \"b\"};"),
    (r"import", "remove every import line: everything is built in."),
    (r"needs your own function", "add that function to the sketch, exactly with that name and signature."),
    (r"(\w+)\(\) is not defined", "{0}() does not exist here.{dym} Write it yourself as a function in the "
     "sketch, or use a function from the reference."),
    (r"(\w+) is not defined", "{0} is not declared.{dym} If it is your own variable, add the line "
     "`float {0} = 0;` near the top of the sketch, right after the // why line, OUTSIDE setup() and draw(): "
     "then every function can use it and it keeps its value between frames. A loop counter such as "
     "`for (int i = ...)` exists only inside its loop: use it inside the loop's {{ }}."),
    (r"(\w+)\(\) (?:takes|does not take|needs)", "{0}() was called with the wrong arguments.{sig}"),
    (r"the type (\w+) is not available", "{0} does not exist here. Use int, float, boolean, String, arrays, or a class you write."),
    (r"expected a name, found [\d.]+", "Java gives each variable its own value: write int w = 240, h = 128; "
     "(not int w, h = 240, 128;)."),
    (r"\bvoid\b", "a call that returns nothing (void), such as textSize(), fill() or a void function of "
     "your own, is used as a value. Use it as a statement on its own line; for the width of text use "
     "textWidth(s)."),
    (r"incompatible|cannot (assign|convert|cast)", "the types do not match. float values go in float variables; "
     "write int(x) to make a float whole."),
]
KIND_HINTS = {
    "syntax": "look at that line and the one before: a missing ; or ), an extra or missing { }, or a Java "
              "feature this compiler does not know.",
    "budget": "the frame does too much work (more than 4 million steps). Use fewer or smaller loops, step "
              "through pixels 2-4 at a time, check that every loop ends (a for loop over fractions needs "
              "a float counter), and call pattern()/shadeField() at most twice a frame.",
    "deadline": "the frame does too much work. Use fewer or smaller loops, and make sure every loop ends.",
    "memory": "the sketch uses too much memory. Make arrays once, outside draw(), and smaller (a few "
              "thousand numbers); never call new inside draw().",
    "depth": "the recursion goes too deep. Stop it after about 10 levels.",
    "size": "use size(240, 128) (or size(240, 128, P3D)); no other size runs.",
}


def describe_frame(g: bytes, blank: bool) -> str:
    """What is actually in a failing frame, in words a small model can act on."""
    W = 240
    nonwhite = [i for i, v in enumerate(g) if v < 250]
    if blank:
        if not nonwhite:
            return ("Every pixel of that frame is white: nothing you drew landed inside the canvas, or it "
                    "was drawn in white (or covered by background() after it). ")
        darkest = min(g)
        dark = sum(1 for v in g if v < 160)
        xs, ys = [i % W for i in nonwhite], [i // W for i in nonwhite]
        box = f"x {min(xs)}..{max(xs)}, y {min(ys)}..{max(ys)}"
        if darkest >= 180:
            return (f"That frame has {len(nonwhite)} drawn pixels ({box}), but the darkest grey is {darkest}: "
                    "light greys look white here. ")
        return (f"That frame has only {dark} dark pixels (all drawing is within {box}); "
                "it needs more than about 100. ")
    light = sum(1 for v in g if v >= 160)
    return f"Only {light} of the {len(g)} pixels of that frame are light. "


def feedback(r: dict, code: str, template: str, replay=None, frame=None) -> tuple[str, str]:
    """(the message for the model, a short label for the log)."""
    e = r.get("error") or {}
    if not isinstance(e, dict):
        e = {"kind": "runtime", "message": str(e), "line": 0}
    kind, msg, line = e.get("kind", "error"), str(e.get("message", "")), int(e.get("line") or 0)
    msg = re.sub(r" \(\w+ arena\)", "", re.sub(r" on the \w+$", " on this VM", msg))
    if kind == "runtime" and not line and replay:
        got = replay()
        if got:
            line, msg = got
    hint = ""
    if kind == "picture":
        fm = re.search(r"frame (\d+)", msg)
        when = f"frame {fm.group(1)}" if fm else "the picture"
        seen = ""
        if frame:
            g = frame(int(fm.group(1)) if fm else 1)
            if g:
                seen = describe_frame(g, msg.startswith("blank"))
        if msg.startswith("blank"):
            got = re.search(r"([\d.]+)% ink", msg)
            share = float(got.group(1)) if got else 0.0
            more = (f" It has {share:g} %: draw at least {max(2, round(0.6 / max(share, 0.05)))} times as much ink."
                    if share > 0 else "")
            head = f"{when} shows (almost) nothing: more than 0.3 % of the pixels (about 100) must be dark.{more} "
            first = (" Draw it from frame 1 on: a curve drawn with p = loopT(...) is empty at the start, so use "
                     "0.2 + 0.8 * loopT(...); no if (frameCount ...) around the drawing.")
            if seen.startswith("Every pixel"):
                hint = (head + seen + "Work out where each shape is when frameCount = 1 and keep it inside "
                        "x 0..239, y 0..127 (around CX = 120, CY = 64; a point at cos(a) * r needs CX + cos(a) * r). "
                        "Draw with fill(0) or stroke(0) after background(255)." + first)
            elif "darkest grey" in seen:
                hint = head + seen + "Draw with fill(0) to fill(150) and stroke(0); colours become light greys."
            elif seen:
                hint = (head + seen + "Draw bigger or more shapes: sizes of 10 to 100 pixels, strokeWeight(2) "
                        "or more for lines." + first)
            else:
                hint = (head + "Draw bigger shapes in dark greys, fill(0) to fill(150) or stroke(0), on "
                        "background(255): light greys and colours look white here. Keep the shapes inside the "
                        "canvas (x 0..239, y 0..127; a shape at x = cos(a) * r needs CX + cos(a) * r)." + first)
        else:
            hint = (f"{when} is (almost) all black. {seen}Use background(255) and leave most of the paper white; "
                    "draw dark shapes on it, not over everything.")
    elif kind == "unsupported" or kind in ("compile", "syntax"):
        for pat, h in HINTS:
            m = re.search(pat, msg)
            if m:
                g = m.groups() or ("",)
                d = did_you_mean(g[0]) if g[0] else ""
                sg = ref_index()["sig"].get(g[0], "") if g[0] else ""
                hint = (h.replace("{{", "{").replace("}}", "}").replace("{0}", g[0] or "")
                        .replace("{dym}", f" Did you mean {d}?" if d else "")
                        .replace("{sig}", f" It is written {sg}." if sg else " Look it up in the reference."))
                break
    if not hint and kind == "runtime":
        hint = {"index": "an index is outside its array. Keep 0 <= i < array.length (check loop bounds and +1/-1 neighbours).",
                "zero": "a division by zero. Make sure the divisor can never be 0.",
                "null": "something is used before it is made: create objects and arrays before using them."}.get(
            next((k for k in ("index", "zero", "null") if k in msg), ""), "fix the failing line.")
    if not hint:
        hint = KIND_HINTS.get(kind, "fix the code at that line.")
    problem = f"Problem: {kind}: {msg}" if kind != "picture" else f"Problem: the frame is not a picture ({msg})."
    quote = ""
    lines = code.split("\n")
    if 0 < line <= len(lines):
        lo, hi = max(1, line - 1), min(len(lines), line + 1)
        quote = "\n".join(f"{'>>' if k == line else '  '} {k:3d} | {lines[k - 1]}" for k in range(lo, hi + 1)) + "\n"
        quote = f"At line {line}:\n" + quote
    name = re.match(r"(\w+)(\(\))? (?:is not defined|takes|does not take|needs)", msg)
    if name and line:
        pat = re.compile(r"\b" + re.escape(name.group(1)) + (r"\s*\(" if name.group(2) else r"\b"))
        others = [k + 1 for k, ln in enumerate(lines) if k + 1 != line and pat.search(ln.split("//")[0])]
        if others:
            hint += (" The same name is also used on line" + ("s " if len(others) > 1 else " ")
                     + ", ".join(map(str, others[:8])) + ": fix every place, not just this one.")
    text = (template.replace("{{PROBLEM}}", problem).replace("{{QUOTE}}", quote).replace("{{HINT}}", hint))
    return re.sub(r"\n{3,}", "\n\n", text), f"{kind}: {msg}"[:120]


# ---------------------------------------------------------------- one request
def one(a, i: int, ctx: dict) -> dict:
    rnd = random.Random(f"{a.seed}:{i}")
    names = pick_examples(ctx["pool"], a.k, rnd, a.example_chars)
    system = system_prompt(ctx["system"], [ctx["pool"][n] for n in names])
    subject = ctx["subjects"][i % len(ctx["subjects"])] if ctx["subjects"] else a.subject
    subject = None if subject == "*" else subject
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_prompt(ctx["user"], subject)}]
    rec = {"request": i, "examples": names, "subject": subject, "tries": [], "ok": False, "tokens_in": 0,
           "tokens_out": 0, "system_chars": len(system)}
    t0 = time.time()
    for t in range(a.retries + 1):
        try:
            ans = chat(a, messages, seed=rnd.randrange(1 << 30))
        except Exception as e:  # noqa: BLE001 - a dead endpoint ends this request, not the run
            rec["tries"].append({"error": f"endpoint: {e}"})
            break
        rec["tokens_in"] += int(ans["usage"].get("prompt_tokens") or 0)
        rec["tokens_out"] += int(ans["usage"].get("completion_tokens") or 0)
        code, notes = extract(ans["text"])
        path = os.path.join(a.out, "attempts", f"{i:03d}-{t}.pde")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code if code else ans["text"])
        if code is None:
            fb = ("Your answer had no sketch in it. Answer with one code block that starts with the "
                  "// piece line, then the // why line, then the sketch (void setup() and void draw()).")
            rec["tries"].append({"ok": False, "error": "no sketch", "notes": notes, "finish": ans["finish"]})
        else:
            r = ctx["checker"].check(path)
            entry = {"ok": r["ok"], "stage": r.get("stage"), "notes": notes, "finish": ans["finish"]}
            if r["ok"]:
                title = (PIECE.search(code).group(1) if PIECE.search(code) else "") or f"untitled-{i}"
                base = slug(title)
                with ctx["lock"]:
                    k = 2
                    name = base
                    while name in ctx["taken"]:
                        name, k = f"{base}-{k}", k + 1
                    ctx["taken"].add(name)
                dst = os.path.join(a.out, "pass", name)
                with open(dst + ".pde", "w", encoding="utf-8") as f:
                    f.write(code)
                ctx["checker"].check(dst + ".pde", sheet=dst + ".png")
                ctx["checker"].check(dst + ".pde", png=dst + ".last.png", scale=1)
                rec.update(ok=True, file=os.path.relpath(dst + ".pde", a.out), title=title,
                           still=r.get("still"), animated=r.get("animated"), max_steps=r.get("max_steps"))
                rec["tries"].append(entry)
                break
            fb, label = feedback(r, code, ctx["feedback"],
                                 replay=lambda: ctx["checker"].runtime_line(path, int(r.get("frames") or 1)),
                                 frame=lambda k: ctx["checker"].frame_grey(path, k))
            entry["error"] = label
            rec["tries"].append(entry)
        if ans["finish"] == "length":
            fb += "\nYour answer was cut off: write a shorter sketch."
        # keep the context small: the request, the last answer and the feedback on it
        messages = messages[:2] + [{"role": "assistant", "content": ans["text"]}, {"role": "user", "content": fb}]
    rec["seconds"] = round(time.time() - t0, 1)
    rec["first_try_ok"] = bool(rec["tries"]) and rec["tries"][0].get("ok", False)
    return rec


# ---------------------------------------------------------------- PNG (8-bit grey only, as the checker writes)
def png_read(path: str) -> tuple[int, int, bytes]:
    data = read_bytes(path)
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    return w, h, b"".join(raw[y * (w + 1) + 1:(y + 1) * (w + 1)] for y in range(h))


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def png_write(path: str, w: int, h: int, px: bytes) -> None:
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    raw = b"".join(b"\x00" + px[y * w:(y + 1) * w] for y in range(h))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def overview(paths: list[str], out: str, cols: int = 4, gap: int = 6) -> None:
    tiles = [png_read(p) for p in paths]
    if not tiles:
        return
    tw, th = tiles[0][0], tiles[0][1]
    cols = min(cols, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    W, H = cols * tw + (cols + 1) * gap, rows * th + (rows + 1) * gap
    buf = bytearray([128]) * (W * H)
    for k, (w, h, px) in enumerate(tiles):
        ox, oy = gap + (k % cols) * (tw + gap), gap + (k // cols) * (th + gap)
        for y in range(min(h, th)):
            buf[(oy + y) * W + ox:(oy + y) * W + ox + min(w, tw)] = px[y * w:y * w + min(w, tw)]
    png_write(out, W, H, bytes(buf))


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--endpoint", default="http://localhost:8080/v1", help="OpenAI-compatible base URL")
    ap.add_argument("--model", default="default", help="model name the endpoint expects")
    ap.add_argument("--key-env", default="OPENAI_API_KEY", help="environment variable holding the API key")
    ap.add_argument("-n", type=int, default=10, help="how many requests")
    ap.add_argument("--retries", type=int, default=3, help="fix attempts after the first answer")
    ap.add_argument("-k", type=int, default=3, help="examples per request, drawn at random")
    ap.add_argument("--example-chars", type=int, default=4000, help="character budget for the examples")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--subject", help="one subject for every request (default: the model's own choice)")
    ap.add_argument("--subjects", help="a file with one subject per line, used in turn (a line * = free choice)")
    ap.add_argument("--system", default=os.path.join(PROMPTS, "system.md"),
                    help="system prompt template (system-full.md: the full references)")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-p", type=float, default=0.95)
    ap.add_argument("--max-tokens", type=int, default=2000)
    ap.add_argument("--timeout", type=float, default=900, help="seconds per model call")
    ap.add_argument("-j", "--parallel", type=int, default=1, help="requests at once")
    ap.add_argument("--checker", help="checker command (default: immanence, else python3 -m immanence)")
    ap.add_argument("--vm", help="path of the native VM runner, passed to the checker")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--out", help="output folder (default: generated/<time>)")
    ap.add_argument("--dry-run", action="store_true", help="print the first request's prompts and their size, then stop")
    a = ap.parse_args(argv)

    pool = load_examples()
    ctx = {"pool": pool, "system": read(a.system), "user": read(os.path.join(PROMPTS, "user.md")),
           "feedback": read(os.path.join(PROMPTS, "feedback.md")), "lock": threading.Lock(), "taken": set(),
           "subjects": [s.strip() for s in read(a.subjects).splitlines() if s.strip()] if a.subjects else []}
    if a.dry_run:
        rnd = random.Random(f"{a.seed}:0")
        names = pick_examples(pool, a.k, rnd, a.example_chars)
        s = system_prompt(ctx["system"], [pool[n] for n in names])
        u = user_prompt(ctx["user"], ctx["subjects"][0] if ctx["subjects"] else a.subject)
        print(s + "\n---- user ----\n" + u)
        print(f"\n[system {len(s)} chars ~{len(s) // 4} tokens; user {len(u)} chars; examples {names}]", file=sys.stderr)
        return 0
    a.out = a.out or os.path.join("generated", time.strftime("%Y%m%d-%H%M%S"))
    for d in ("pass", "attempts"):
        os.makedirs(os.path.join(a.out, d), exist_ok=True)
    ctx["checker"] = Checker(resolve_checker(a.checker), a.vm, a.frames)
    recs = []
    with cf.ThreadPoolExecutor(max(1, a.parallel)) as ex, open(os.path.join(a.out, "log.jsonl"), "w") as log:
        def safe(i):
            try:
                return one(a, i, ctx)
            except Exception as e:  # noqa: BLE001 - one broken request never ends the run
                return {"request": i, "ok": False, "first_try_ok": False, "tries": [{"error": f"generate.py: {e!r}"}],
                        "tokens_in": 0, "tokens_out": 0, "seconds": 0.0}
        for rec in ex.map(safe, range(a.n)):
            recs.append(rec)
            log.write(json.dumps(rec) + "\n")
            log.flush()
            errs = "; ".join(t.get("error", "") for t in rec["tries"] if not t.get("ok"))
            print(f"[{rec['request']:3d}] {'PASS' if rec['ok'] else 'fail'} after {len(rec['tries'])} "
                  f"answer(s), {rec['seconds']:.0f} s  {rec.get('title', '')}  {errs[:160]}", flush=True)
    n = len(recs)
    fails = {}
    for r in recs:
        for t in r["tries"]:
            if not t.get("ok"):
                k = re.sub(r"\d+", "N", str(t.get("error", "?")))[:90]
                fails[k] = fails.get(k, 0) + 1
    passing = [r for r in recs if r["ok"]]
    summary = {"requests": n, "first_try_pass": sum(r["first_try_ok"] for r in recs),
               "pass_after_retries": len(passing),
               "answers": sum(len(r["tries"]) for r in recs),
               "mean_answers_to_pass": round(sum(len(r["tries"]) for r in passing) / max(1, len(passing)), 2),
               "tokens_in": sum(r["tokens_in"] for r in recs), "tokens_out": sum(r["tokens_out"] for r in recs),
               "failures": dict(sorted(fails.items(), key=lambda kv: -kv[1])),
               "model": a.model, "k": a.k, "retries": a.retries, "seed": a.seed,
               "system": os.path.basename(a.system)}
    with open(os.path.join(a.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    lasts = [os.path.join(a.out, r["file"][:-4] + ".last.png") for r in passing]
    overview([p for p in lasts if os.path.exists(p)], os.path.join(a.out, "overview.png"))
    print(f"\n{summary['first_try_pass']}/{n} passed on the first answer, {len(passing)}/{n} after up to "
          f"{a.retries} fixes. Output: {a.out}")
    for k, v in list(summary["failures"].items())[:8]:
        print(f"  {v:3d}  {k}")
    return 0 if passing else 1


if __name__ == "__main__":
    sys.exit(main())
