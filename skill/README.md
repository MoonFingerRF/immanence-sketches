# Sketch-writing skill

Everything a language model needs to write new Processing sketches that compile and run on this
repository's VM (240 x 128 greyscale, the bytecode VM in `src/`), and a loop that checks what it
writes and sends the errors back until the sketch passes. It is written so that small models
(1-8B parameters) can use it, not only large ones.

Two forms of the same knowledge:

| form | for | files |
|---|---|---|
| Agent Skill | agents that can run commands and read files and load Agent Skills (a folder with a `SKILL.md`) | `sketch-writer/` |
| Plain prompt + runner | any chat model behind an OpenAI-compatible endpoint | `prompt/`, `generate.py` |

```
skill/
  sketch-writer/
    SKILL.md                 the instructions: write -> check -> read the error -> fix -> re-check
    reference/language.md    the Processing subset the compiler accepts, what it does not, limits, pitfalls
    reference/library.md     the built-in library (BitMapMathLib)
    examples/*.pde           30 sketches that pass the check
    scripts/check.sh         runs the checker; quotes the failing line
  prompt/
    system.md                the whole instruction + a compact reference + 3 examples (8,700 characters:
                             ~2,200 tokens by the 4-characters rule, ~2,800 real tokens; code is dense)
    system-full.md           the same with the full references (19,000 characters: ~4,750 / ~6,100 tokens)
    build_full.py            rebuilds system-full.md from system.md and the references
    user.md                  the request: a subject, or the model's own choice
    feedback.md              what the model reads after a failed check
  generate.py                the write -> check -> fix loop against any OpenAI-compatible endpoint
```

## Quick start

From the repository root, with the native runner built (`make -C host`):

```sh
export PYTHONPATH=$PWD/python IMMANENCE_VM=$PWD/host/build/immanence-vm

# check a sketch by hand
skill/sketch-writer/scripts/check.sh skill/sketch-writer/examples/tally.pde --sheet /tmp/tally.png

# let a local model write ten sketches (llama.cpp's server here; any OpenAI-compatible endpoint works)
llama-server -m some-small-instruct-model.gguf --host 127.0.0.1 --port 8080 -c 16384 --jinja &
python3 skill/generate.py --endpoint http://127.0.0.1:8080/v1 --model local -n 10 --retries 3
```

`generate.py` prints one line per request and writes `generated/<time>/`: the passing sketches
with an 8-frame contact sheet each (`pass/`), every answer as checked (`attempts/`), `log.jsonl`,
`summary.json` (first-try and after-retries pass rates, the commonest failures) and `overview.png`
(the last frame of every passing sketch).

Useful options: `--subject "tides"` or `--subjects file.txt` (one per line, `*` = free choice),
`-k 3` examples per request drawn at random from `sketch-writer/examples/` (seeded by `--seed`,
within `--example-chars`), `--system prompt/system-full.md` for the full references, `-j 2`
parallel requests, `--temperature`, `--max-tokens`, `--checker "python3 -m immanence"`,
`--vm PATH`, `--dry-run` to print the first request's prompts and their size.

## Plugging in other models

* **Any OpenAI-compatible server** (llama.cpp `llama-server`, vLLM, SGLang, LM Studio, Ollama at
  `http://localhost:11434/v1`, hosted APIs): pass `--endpoint` (the base URL ending in `/v1`) and
  `--model` (the name the server expects). If it needs a key, put it in `$OPENAI_API_KEY` or name
  another variable with `--key-env`.
* **A chat UI or anything else**: paste `prompt/system.md` as the system message and
  `prompt/user.md` (with `{{SUBJECT}}` filled in) as the user message; check the answer with
  `scripts/check.sh` and paste the error back with the failing line.
* **An agent**: install `sketch-writer/` as a skill (copy the folder into the agent's skills
  directory). The agent reads `SKILL.md`, the references and examples on
  demand, and runs `scripts/check.sh` itself.
* **Context size**: a request with `system.md` and three examples is about 3,200 real tokens, and an
  answer 300-800 more; a retry adds the last answer and the feedback (about 1,000). With a 4k-context
  model use `-k 1` or `-k 0` (`-k 0` keeps the three examples written into `system.md`); with 8k or
  more, `system-full.md` (about 6,500 tokens with three examples) fits.
* **Reasoning models**: `<think>...</think>` blocks are removed before the code is read; give them a
  larger `--max-tokens`.

## How the loop talks to the model

The answer's code block is taken (text around it, a missing `// piece` header and prose after the
last brace are cleaned up), written to a file and checked: compile, 60 frames on the VM, every
frame a picture. On a failure the model gets one short message: the kind of problem, the
checker's message, the failing line quoted with its neighbours and line numbers, and one concrete
fix (for example, for `ArrayList`: "use a fixed-size array made once at the top, and a count").
A runtime fault on the VM has no line number, so the sketch is replayed on the Python reference
interpreter to find it. Only the original request, the last answer and the feedback are sent, so
the context stays small however many retries there are.

## Adding examples

Any sketch that passes the check can go in `sketch-writer/examples/` as `<title-slug>.pde`,
starting with its `// piece "title"` and `// why "..."` lines. Keep them short (under about 1,900
characters) so a few fit in a small model's context. The examples show what the medium can do;
the prompts tell the model to make its own piece and not to copy them.
