# The immanencepure.xyz API

Every piece on [immanencepure.xyz](https://immanencepure.xyz) is also available as plain files:
its Processing source, its title and description, its precompiled bytecode, and a few facts a
device needs before running it. It is a static API: no keys, no rate limits beyond the CDN's,
`GET` only, and every response carries `Access-Control-Allow-Origin: *`, so a web page on any
site can use it too.

Base URL: `https://immanencepure.xyz/api/v1`

| Request | Returns |
|---|---|
| `GET /index.json` | the list of all pieces, with the fields of each piece below except the hashes, sizes and golden frames |
| `GET /info/<id>.json` | everything about one piece |
| `GET /source/<id>.pde` | the raw Processing source (`text/plain; charset=utf-8`) |
| `GET /bytecode/<id>.pdeb` | the precompiled program for the VM (`application/octet-stream`) |
| `GET /random` | a randomly chosen piece's info (as `info/<id>.json`); see *A random piece* |
| `GET /random.pde`, `GET /random.pdeb` | a random piece's source, or its bytecode |

## `index.json`

```json
{
  "api": 1,
  "generated": "2026-09-29T05:00:16Z",
  "site": "https://immanencepure.xyz",
  "bytecode": {"magic": "PDEB", "version": 1},
  "canvas": {"width": 240, "height": 128},
  "license": ...,
  "docs": "https://github.com/MoonFingerRF/immanence-sketches/blob/main/docs/API.md",
  "random": "/api/v1/random",
  "count": 2227,
  "pieces": [
    {
      "id": "megaphone-b3f616",
      "title": "megaphone",
      "description": "Shout through a horn and it goes one way only, in a beam you could nearly see.",
      "still": false, "clock": false, "library": true, "p3d": false,
      "bytecode_bytes": 2825, "arena_bytes": 524288,
      "info": "/api/v1/info/megaphone-b3f616.json",
      "source": "/api/v1/source/megaphone-b3f616.pde",
      "bytecode": "/api/v1/bytecode/megaphone-b3f616.pdeb"
    }
  ]
}
```

(The values above show the shape; the ids and titles are illustrative.)

## A piece: `info/<id>.json`

| Field | Meaning |
|---|---|
| `id` | the piece's permanent id (see *Stability*) |
| `title`, `description` | as the site shows them; the source's `// piece "..."` and `// why "..."` lines |
| `still` | `true`: the piece draws one frame and stops |
| `clock` | `true`: it reads the wall clock (`hour()`, `minute()` ...); set `vm.api.now_s` (and `tz_s`) on the device |
| `library` | `true`: it uses BitMapMathLib (already linked into the bytecode; for desktop Processing, `immanence link` the source) |
| `p3d` | `true`: it draws in 3D (`size(240, 128, P3D)`): the VM then needs a 120 KB depth buffer |
| `bytecode_bytes`, `bytecode_sha256` | size and SHA-256 of the program |
| `source_bytes`, `source_sha256` | size and SHA-256 of the source |
| `arena_bytes` | the smallest power-of-two arena measured to draw this piece's first 240 frames exactly (see docs/MEMORY.md); also in `index.json` |
| `golden` | `{"frames": n, "crc32": [...]}`: crc32 of the 240 × 128 grey canvas for the first frames (up to 30), so a device can prove it draws the piece exactly; `null` for pieces that read the clock |
| `info`, `source`, `bytecode` | the URLs of the three files (absolute paths on the site) |

Before running a downloaded program, check byte 4 against your VM's `pde::BC_VERSION` (or
`index.json`'s `bytecode.version`): a VM refuses programs of another bytecode version.

## A random piece

`GET /random` (or `/random.pde`, `/random.pdeb`) returns a piece chosen at random, in one
request. The chosen piece's id is in the `X-Piece-Id` response header (exposed to browsers) and
its permanent URL in `Content-Location`; responses are never cached. Optional filters narrow the
choice, all of which must hold:

| Parameter | Keeps pieces where |
|---|---|
| `still=true\|false` | the piece is a still picture, or animated |
| `clock=true\|false` | it reads the wall clock, or not |
| `library=true\|false` | it uses BitMapMathLib, or not |
| `p3d=true\|false` | it draws in 3D, or not (`p3d=false` on boards without 120 KB for the depth buffer) |
| `max_arena=<bytes>` | its measured arena fits this size (pick the memory you can give the VM) |
| `max_bytes=<bytes>` | its bytecode is at most this size |
| `seed=<text>` | not a filter: the same seed always picks the same piece among the filtered ones (a piece of the day: `seed=2026-11-27`) |

No match gives `404 {"error": ...}`; a malformed filter gives `400`.

```sh
curl -sD - -o piece.pdeb "https://immanencepure.xyz/api/v1/random.pdeb?p3d=false&max_arena=131072" | grep -i x-piece-id
immanence fetch random --p3d false --max-arena 131072     # writes <id>.pde, .pdeb and .json
```

## Stability (what v1 promises)

* A piece's `id` never changes meaning: once published it always names the same piece, even if
  its title is edited or pieces are added, removed or reordered.
* The files of a piece may be revised (a fix to its source recompiles its bytecode): compare
  `bytecode_sha256` to notice. A removed piece's URLs return 404.
* Fields may be added to the JSON; none of the fields above will be removed or change meaning in
  v1. A breaking change gets a new path (`/api/v2/`), and v1 keeps working alongside it.
* The bytecode version can change only with a new major version of the library; `bytecode.version`
  says which one the files are.

## Examples

### curl

```sh
curl -s https://immanencepure.xyz/api/v1/index.json | python3 -c \
  'import json,sys; [print(p["id"], p["title"]) for p in json.load(sys.stdin)["pieces"][:10]]'
curl -sO https://immanencepure.xyz/api/v1/source/<id>.pde
curl -sO https://immanencepure.xyz/api/v1/bytecode/<id>.pdeb
```

### Python

```python
import json, urllib.request

API = "https://immanencepure.xyz/api/v1"
get = lambda path: urllib.request.urlopen(API + path).read()

index = json.loads(get("/index.json"))
small = [p for p in index["pieces"] if not p["p3d"] and p["bytecode_bytes"] < 1500]
piece = json.loads(get(f"/info/{small[0]['id']}.json"))
program = get(f"/bytecode/{piece['id']}.pdeb")
assert program[4] == index["bytecode"]["version"]
print(piece["title"], len(program), "bytes, arena", piece["arena_bytes"])
```

Or with the command line tool: `immanence fetch list`, then `immanence fetch <id>` (writes
`<id>.pde`, `<id>.pdeb` and `<id>.json`), or `immanence fetch random [--p3d false --max-arena N]`.

### ESP32

[`examples/fetch-from-site`](../examples/fetch-from-site) downloads `info/<id>.json` and
`bytecode/<id>.pdeb` over HTTPS (with the ESP32's certificate bundle), checks the version and the
arena size, and runs the piece. The essential part:

```cpp
HTTPClient http;
http.begin(client, "https://immanencepure.xyz/api/v1/bytecode/" + id + ".pdeb");
if (http.GET() == 200) {
  int n = http.getSize();
  uint8_t *prog = (uint8_t *) ps_malloc(n);
  http.getStream().readBytes(prog, n);
  if (prog[4] == pde::BC_VERSION && vm->load(prog, n)) { /* run it */ }
}
```

### A web page

```js
const api = "https://immanencepure.xyz/api/v1";
const { pieces } = await (await fetch(`${api}/index.json`)).json();
const src = await (await fetch(`${api}/source/${pieces[0].id}.pde`)).text();

const r = await fetch(`${api}/random.pde?p3d=false`);      // a random piece, in one request
console.log(r.headers.get("X-Piece-Id"), await r.text());
```

## Licence

The pieces' sources and bytecode are MIT-licensed, like this repository.
