/*
 * Processing -> VM bytecode in JavaScript: an exact port of the Python compiler (python/immanence/pde).
 *
 * No DOM or network dependencies: it runs in a browser (immanencepure.xyz uses it in its editor's
 * compiler worker) and in Node. `library` is BitMapMathLib's chunk table (js/mathlib.json, written
 * by `python3 -m immanence.mathlib json`); compileBytecode links the chunks a sketch calls, with
 * their original line numbers, exactly as the Python compiler does. The output is byte-identical
 * to the Python compiler's for every piece on immanencepure.xyz (js/README.md).
 */

const MAX_SOURCE = 16 * 1024;
const MAX_TOKENS = 12000;
const MAX_NUMBER_CHARS = 80;
const MAX_NEST = 40;
const MAX_FUNCS = 4096;
const MAX_CODE = 65535;
const NOCONST = Symbol("not-constant");

export class PdeError extends Error {
  constructor(kind, message, line = 0) {
    super(message);
    this.name = "PdeError";
    this.kind = kind;
    this.line = line || 0;
  }
}

export class Unsupported extends Error {
  constructor(what, line = 0) {
    super(`${what} (line ${line})`);
    this.name = "Unsupported";
    this.kind = "unsupported";
    this.what = what;
    this.line = line || 0;
  }
}

const OPS_LEX = `>>>= <<= >>= >>> ... -> :: ++ -- && || == != <= >= += -= *= /= %= &= |= ^= << >>
( ) { } [ ] ; , . = > < ! ~ ? : + - * / & | ^ % @`.split(/\s+/).sort((a, b) => b.length - a.length);
const OP_FIRST = new Set(OPS_LEX.map((x) => x[0]));
const ESC = { n: "\n", t: "\t", r: "\r", b: "\b", f: "\f", 0: "\0", "\\": "\\", "'": "'", '"': '"' };
const alpha = (c) => !!c && (c === "_" || c === "$" || /[\p{L}]/u.test(c));
const alnum = (c) => !!c && (c === "_" || c === "$" || /[\p{L}\p{N}]/u.test(c));
const digit = (c) => !!c && c >= "0" && c <= "9";

function wrap32(v) { return Number(BigInt.asIntN(32, BigInt(v))); }
function wrap64(v) { return BigInt.asIntN(64, BigInt(v)); }
function f32(v) { return Math.fround(v); }

function escapeAt(src, i, line) {
  if (i >= src.length) throw new PdeError("syntax", "unfinished escape", line);
  const c = src[i];
  if (c === "u") {
    let j = i + 1;
    while (j < src.length && src[j] === "u") j++;
    const hex = src.slice(j, j + 4);
    if (!/^[0-9a-fA-F]{4}$/.test(hex)) throw new PdeError("syntax", "bad \\u escape", line);
    return [String.fromCharCode(parseInt(hex, 16)), j + 4];
  }
  if (/[0-7]/.test(c)) {
    let j = i;
    while (j < src.length && j < i + 3 && /[0-7]/.test(src[j])) j++;
    return [String.fromCharCode(parseInt(src.slice(i, j), 8) & 255), j];
  }
  if (Object.hasOwn(ESC, c)) return [ESC[c], i + 1];
  throw new PdeError("syntax", `bad escape \\${c}`, line);
}

function readNumber(src, i, line, out) {
  let end = i;
  while (end < src.length && (alnum(src[end]) || src[end] === ".")) end++;
  if (end - i > MAX_NUMBER_CHARS && !src.startsWith("...", i))
    throw new PdeError("syntax", "number too long", line);
  try {
    if (src.startsWith("0x", i) || src.startsWith("0X", i)) {
      let j = i + 2;
      while (j < src.length && /[0-9a-fA-F_]/.test(src[j])) j++;
      const s = src.slice(i + 2, j).replaceAll("_", "");
      if (!s) throw new PdeError("syntax", "bad hex number", line);
      const v = BigInt(`0x${s}`);
      if (j < src.length && /[lL]/.test(src[j])) { out.push(["long", wrap64(v), line]); return j + 1; }
      if (v > 0xffffffffn) throw new PdeError("syntax", "integer number too large", line);
      out.push(["int", wrap32(v), line]); return j;
    }
    if (src.startsWith("0b", i) || src.startsWith("0B", i)) {
      let j = i + 2;
      while (j < src.length && /[01_]/.test(src[j])) j++;
      const s = src.slice(i + 2, j).replaceAll("_", "") || "0";
      const v = BigInt(`0b${s}`);
      if (j < src.length && /[lL]/.test(src[j])) { out.push(["long", wrap64(v), line]); return j + 1; }
      out.push(["int", wrap32(v), line]); return j;
    }
    let j = i;
    while (j < src.length && (digit(src[j]) || src[j] === "_")) j++;
    let isFloat = false;
    if (j < src.length && src[j] === "." && !src.startsWith("...", j)) {
      isFloat = true; j++;
      while (j < src.length && (digit(src[j]) || src[j] === "_")) j++;
    }
    if (j < src.length && /[eE]/.test(src[j])) {
      let k = j + 1;
      if (k < src.length && /[+-]/.test(src[k])) k++;
      if (k < src.length && digit(src[k])) {
        isFloat = true; j = k;
        while (j < src.length && digit(src[j])) j++;
      }
    }
    const text = src.slice(i, j).replaceAll("_", "");
    if (j < src.length && /[fFdD]/.test(src[j])) {
      out.push([/[dD]/.test(src[j]) ? "double" : "float", Number(text), line]); return j + 1;
    }
    if (isFloat) { out.push(["float", Number(text), line]); return j; }
    if (j < src.length && /[lL]/.test(src[j])) { out.push(["long", wrap64(BigInt(text)), line]); return j + 1; }
    let v;
    if (text.length > 1 && text[0] === "0" && /^[0-7]+$/.test(text)) v = BigInt(`0o${text.slice(1) || "0"}`);
    else v = BigInt(text);
    if (v > 0x80000000n) throw new PdeError("syntax", "integer number too large", line);
    out.push(["int", Number(v), line]); return j;
  } catch (e) {
    if (e instanceof PdeError) throw e;
    throw new PdeError("syntax", "bad number", line);
  }
}

export function tokenize(src, firstLine = 1, bounded = true) {
  if (bounded && src.length > MAX_SOURCE)
    throw new PdeError("syntax", `the sketch is longer than ${MAX_SOURCE >> 10} KB`);
  const out = [];
  let i = 0, line = firstLine;
  while (i < src.length) {
    const c = src[i];
    if (c === "\n") { line++; i++; continue; }
    if (/[ \t\r\f\v\uFEFF\u00A0]/.test(c)) { i++; continue; }
    if (c === "/" && src[i + 1] === "/") { while (i < src.length && src[i] !== "\n") i++; continue; }
    if (c === "/" && src[i + 1] === "*") {
      const j = src.indexOf("*/", i + 2);
      if (j < 0) throw new PdeError("syntax", "unfinished /* comment", line);
      for (let k = i; k < j; k++) if (src[k] === "\n") line++;
      i = j + 2; continue;
    }
    if (bounded && out.length >= MAX_TOKENS) throw new PdeError("syntax", "the sketch is too long");
    if (alpha(c)) {
      let j = i + 1; while (j < src.length && alnum(src[j])) j++;
      out.push(["id", src.slice(i, j), line]); i = j; continue;
    }
    if (digit(c) || (c === "." && digit(src[i + 1]))) { i = readNumber(src, i, line, out); continue; }
    if (c === "#" && /^[0-9a-fA-F]{6}/.test(src.slice(i + 1, i + 7))) {
      out.push(["color", (0xff000000 | parseInt(src.slice(i + 1, i + 7), 16)) >>> 0, line]); i += 7; continue;
    }
    if (c === '"') {
      let j = i + 1, s = "";
      for (;;) {
        if (j >= src.length || src[j] === "\n") throw new PdeError("syntax", "unfinished string", line);
        if (src[j] === '"') break;
        if (src[j] === "\\") { const r = escapeAt(src, j + 1, line); s += r[0]; j = r[1]; }
        else s += src[j++];
      }
      out.push(["str", s, line]); i = j + 1; continue;
    }
    if (c === "'") {
      let j = i + 1, ch;
      if (src[j] === "\\") { const r = escapeAt(src, j + 1, line); ch = r[0]; j = r[1]; }
      else if (j < src.length) ch = src[j++];
      else throw new PdeError("syntax", "unfinished character", line);
      if (j >= src.length || src[j] !== "'") throw new PdeError("syntax", "a character literal holds one character", line);
      out.push(["char", ch.charCodeAt(0), line]); i = j + 1; continue;
    }
    if (OP_FIRST.has(c)) {
      const op = OPS_LEX.find((x) => src.startsWith(x, i));
      if (op) { out.push(["op", op, line]); i += op.length; continue; }
    }
    if (c === "“" || c === "”") {
      const j = src.indexOf(c === "“" ? "”" : "“", i + 1), k = src.indexOf("\n", i + 1);
      if (j > 0 && (k < 0 || j < k)) { out.push(["str", src.slice(i + 1, j), line]); i = j + 1; continue; }
    }
    throw new PdeError("syntax", `unexpected character ${JSON.stringify(c)}`, line);
  }
  out.push(["eof", null, line]);
  return out;
}

const PRIMS = ["int", "float", "double", "long", "boolean", "char", "byte", "short", "void", "color"];
const KEYWORDS = new Set(["if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "return",
  "new", "class", "true", "false", "null", "this", "static", "final", "public", "private", "protected", "import", "extends",
  "implements", "interface", "try", "catch", "finally", "throw", "throws", "super", "instanceof", "abstract", "synchronized",
  "volatile", "transient", "native", "enum", "package", "goto", "const", "assert"]);
const MODIFIERS = new Set(["static", "final", "public", "private", "protected", "abstract", "synchronized", "volatile", "transient", "native"]);
const ASSIGN_OPS = new Set(["=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", ">>>="]);
const BINARY = { "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
  "<<": 8, ">>": 8, ">>>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10 };
const prim = (t) => t === "color" ? "int" : t;

class Parser {
  constructor(src, classes = [], firstLine = 1, bounded = true) {
    this.toks = tokenize(src, firstLine, bounded); this.i = 0; this.depth = 0; this.classes = new Set(classes);
    for (let k = 0; k + 1 < this.toks.length; k++)
      if (this.toks[k][0] === "id" && this.toks[k][1] === "class" && this.toks[k + 1][0] === "id") this.classes.add(this.toks[k + 1][1]);
  }
  peek(k = 0) { return this.toks[Math.min(this.i + k, this.toks.length - 1)]; }
  get line() { return this.peek()[2]; }
  next() { const t = this.toks[this.i]; if (t[0] !== "eof") this.i++; return t; }
  isOp(v, k = 0) { const t = this.peek(k); return t[0] === "op" && t[1] === v; }
  isId(v = null, k = 0) { const t = this.peek(k); return t[0] === "id" && (v === null || t[1] === v); }
  accept(v) { if (this.isOp(v) || this.isId(v)) { this.i++; return true; } return false; }
  expect(v) { if (!this.accept(v)) { const t = this.peek(), got = t[0] === "eof" ? "the end" : JSON.stringify(t[1]); throw new PdeError("syntax", `expected ${JSON.stringify(v)}, found ${got}`, t[2]); } }
  ident() { const t = this.peek(); if (t[0] !== "id" || KEYWORDS.has(t[1])) throw new PdeError("syntax", `expected a name, found ${JSON.stringify(t[1])}`, t[2]); this.i++; return t[1]; }
  enter() { if (++this.depth > MAX_NEST) throw new PdeError("depth", "the code is nested too deeply", this.line); }
  leave() { this.depth--; }

  tryType() {
    const start = this.i, tok = this.peek(); if (tok[0] !== "id") return null;
    let name = tok[1], ty;
    if (PRIMS.includes(name)) { this.i++; ty = prim(name); }
    else if (KEYWORDS.has(name)) return null;
    else {
      this.i++; let args = [];
      if (this.isOp("<")) { const got = this.typeArgs(); if (got === null) { this.i = start; return null; } args = got; }
      while (this.isOp(".") && this.isId(null, 1) && /^[a-z]/.test(name)) {
        this.i++; name = this.ident();
        if (this.isOp("<")) { const got = this.typeArgs(); if (got === null) { this.i = start; return null; } args = got; }
      }
      ty = name === "String" ? "String" : ["C", name, args];
    }
    while (this.isOp("[") && this.isOp("]", 1)) { this.i += 2; ty = ["[]", ty]; }
    return ty;
  }
  typeArgs() {
    const save = this.i; this.i++; const args = [];
    if (this.isOp(">")) { this.i++; return args; }
    for (;;) {
      let ty;
      if (this.isOp("?")) { this.i++; ty = ["C", "Object", []]; } else ty = this.tryType();
      if (ty === null) { this.i = save; return null; }
      args.push(ty); if (this.accept(",")) continue;
      const t = this.peek();
      if (t[0] === "op" && [">", ">>", ">>>"].includes(t[1])) { if (t[1] === ">") this.i++; else this.toks[this.i] = ["op", t[1].slice(1), t[2]]; return args; }
      this.i = save; return null;
    }
  }
  looksLikeDecl() {
    const save = this.i;
    try {
      while (this.isId() && MODIFIERS.has(this.peek()[1])) this.i++;
      const ty = this.tryType(); if (ty === null) return false;
      const t = this.peek(); return t[0] === "id" && !KEYWORDS.has(t[1]) && this.peek(1)[0] === "op" && ["=", ";", ",", "[", ":", ")"].includes(this.peek(1)[1]);
    } finally { this.i = save; }
  }
  program() {
    const items = [];
    while (this.peek()[0] !== "eof") { if (this.accept(";")) continue; if (this.isId("import")) throw new PdeError("compile", "import is not available here", this.line); items.push(this.member(true)); }
    return items;
  }
  member(top, cls = null) {
    const line = this.line; while (this.isId() && MODIFIERS.has(this.peek()[1])) this.i++;
    if (this.isId("class")) return this.classDecl();
    if (this.isId("interface") || this.isId("enum")) throw new PdeError("compile", `${this.peek()[1]} is not available here`, line);
    if (cls !== null && this.isId(cls) && this.isOp("(", 1)) { this.i++; const params = this.params(); return ["ctor", line, cls, params, this.block()]; }
    const save = this.i, ty = this.tryType();
    if (ty !== null && this.isId() && !KEYWORDS.has(this.peek()[1])) {
      const name = this.peek()[1];
      if (this.isOp("(", 1)) { this.i++; const params = this.params(); if (this.isId("throws")) throw new PdeError("compile", "exceptions are not available here", line); return ["func", line, ty, name, params, this.block()]; }
      this.i = save; const decl = this.varDecl(); this.expect(";"); return ["global", line, decl];
    }
    this.i = save; if (!top) throw new PdeError("syntax", "expected a field or a method in the class", line);
    return ["stmt", line, this.statement()];
  }
  classDecl() {
    const line = this.line; this.expect("class"); const name = this.ident();
    if (this.isId("extends") || this.isId("implements")) throw new PdeError("compile", "extends/implements are not available here", line);
    this.expect("{"); const members = [];
    while (!this.accept("}")) { if (this.peek()[0] === "eof") throw new PdeError("syntax", "unfinished class", line); if (!this.accept(";")) members.push(this.member(false, name)); }
    return ["class", line, name, members];
  }
  params() {
    this.expect("("); const out = [];
    if (!this.accept(")")) for (;;) {
      while (this.isId("final")) this.i++;
      let ty = this.tryType(); if (ty === null) throw new PdeError("syntax", "expected a parameter type", this.line);
      if (this.accept("...")) ty = ["[]", ty]; const name = this.ident();
      while (this.isOp("[") && this.isOp("]", 1)) { this.i += 2; ty = ["[]", ty]; }
      out.push([ty, name]); if (this.accept(")")) break; this.expect(",");
    }
    return out;
  }
  block() {
    const line = this.line; this.expect("{"); this.enter(); const stmts = [];
    while (!this.accept("}")) { if (this.peek()[0] === "eof") throw new PdeError("syntax", "a { is never closed", line); stmts.push(this.statement()); }
    this.leave(); return ["block", line, stmts];
  }
  varDecl() {
    const line = this.line; while (this.isId() && MODIFIERS.has(this.peek()[1])) this.i++;
    const ty0 = this.tryType(); if (ty0 === null) throw new PdeError("syntax", "expected a type", line); const out = [];
    for (;;) {
      const name = this.ident(); let ty = ty0;
      while (this.isOp("[") && this.isOp("]", 1)) { this.i += 2; ty = ["[]", ty]; }
      let init = null; if (this.accept("=")) init = this.isOp("{") ? this.arrayInit() : this.expr();
      out.push([ty, name, init]); if (!this.accept(",")) break;
    }
    return ["vardecl", line, out];
  }
  arrayInit() {
    const line = this.line; this.expect("{"); this.enter(); const items = [];
    while (!this.accept("}")) { items.push(this.isOp("{") ? this.arrayInit() : this.expr()); if (!this.accept(",")) { this.expect("}"); break; } }
    this.leave(); return ["arrinit", line, items];
  }
  statement() { this.enter(); try { return this.statementInner(); } finally { this.leave(); } }
  statementInner() {
    const line = this.line, t = this.peek();
    if (this.isOp("{")) return this.block();
    if (this.accept(";")) return ["empty", line];
    if (t[0] === "id") {
      const w = t[1];
      if (w === "if") { this.i++; this.expect("("); const c = this.expr(); this.expect(")"); const then = this.statement(); const other = this.accept("else") ? this.statement() : null; return ["if", line, c, then, other]; }
      if (w === "while") { this.i++; this.expect("("); const c = this.expr(); this.expect(")"); return ["while", line, c, this.statement()]; }
      if (w === "do") { this.i++; const body = this.statement(); this.expect("while"); this.expect("("); const c = this.expr(); this.expect(")"); this.expect(";"); return ["do", line, body, c]; }
      if (w === "for") return this.forStmt();
      if (w === "switch") return this.switchStmt();
      if (w === "break" || w === "continue") { this.i++; const label = this.isId() && !KEYWORDS.has(this.peek()[1]) ? this.ident() : null; this.expect(";"); return [w, line, label]; }
      if (w === "return") { this.i++; const e = this.isOp(";") ? null : this.expr(); this.expect(";"); return ["return", line, e]; }
      if (["try", "throw", "synchronized", "assert"].includes(w)) throw new PdeError("compile", `${w} is not available here`, line);
      if (w === "class") throw new PdeError("compile", "a class inside a function is not available here", line);
      if (!KEYWORDS.has(w) && this.isOp(":", 1) && !this.looksLikeDecl()) { this.i += 2; return ["labeled", line, w, this.statement()]; }
      if (this.looksLikeDecl()) { const d = this.varDecl(); this.expect(";"); return d; }
    }
    const e = this.expr(); this.expect(";"); return ["expr", line, e];
  }
  forStmt() {
    const line = this.line; this.expect("for"); this.expect("("); const save = this.i;
    while (this.isId("final")) this.i++;
    const ty = this.tryType();
    if (ty !== null && this.isId() && this.isOp(":", 1)) { const name = this.ident(); this.expect(":"); const it = this.expr(); this.expect(")"); return ["foreach", line, ty, name, it, this.statement()]; }
    this.i = save; const init = [];
    if (!this.isOp(";")) {
      if (this.looksLikeDecl()) init.push(this.varDecl());
      else { init.push(["expr", line, this.expr()]); while (this.accept(",")) init.push(["expr", line, this.expr()]); }
    }
    this.expect(";"); const cond = this.isOp(";") ? null : this.expr(); this.expect(";"); const update = [];
    if (!this.isOp(")")) { update.push(this.expr()); while (this.accept(",")) update.push(this.expr()); }
    this.expect(")"); return ["for", line, init, cond, update, this.statement()];
  }
  switchStmt() {
    const line = this.line; this.expect("switch"); this.expect("("); const e = this.expr(); this.expect(")"); this.expect("{"); const cases = [];
    while (!this.accept("}")) {
      if (this.peek()[0] === "eof") throw new PdeError("syntax", "unfinished switch", line);
      let labels = [];
      if (this.accept("default")) { labels = null; this.expect(":"); }
      else { this.expect("case"); labels.push(this.expr()); while (this.accept(",")) labels.push(this.expr()); this.expect(":"); }
      const body = [];
      while (!(this.isId("case") || this.isId("default") || this.isOp("}"))) { if (this.peek()[0] === "eof") throw new PdeError("syntax", "unfinished switch", line); body.push(this.statement()); }
      cases.push([labels, body]);
    }
    return ["switch", line, e, cases];
  }
  expr() { this.enter(); try { return this.assignment(); } finally { this.leave(); } }
  assignment() {
    const left = this.ternary(), t = this.peek();
    if (t[0] === "op" && ASSIGN_OPS.has(t[1])) { if (!["name", "field", "index"].includes(left[0])) throw new PdeError("syntax", "the left of an assignment must be a variable", t[2]); this.i++; const right = this.isOp("{") ? this.arrayInit() : this.expr(); return ["assign", t[2], t[1], left, right]; }
    return left;
  }
  ternary() {
    const c = this.binary(1); if (!this.isOp("?")) return c;
    const line = this.line; this.i++; const a = this.expr(); this.expect(":"); this.enter(); let b; try { b = this.ternary(); } finally { this.leave(); }
    return ["cond", line, c, a, b];
  }
  binary(minPrec) {
    let left = this.unary();
    for (;;) {
      const t = this.peek(); if (t[0] === "id" && t[1] === "instanceof") throw new PdeError("compile", "instanceof is not available here", t[2]);
      if (t[0] !== "op" || !Object.hasOwn(BINARY, t[1]) || BINARY[t[1]] < minPrec) return left;
      const op = t[1], prec = BINARY[op]; this.i++; this.enter(); let right; try { right = this.binary(prec + 1); } finally { this.leave(); }
      left = ["bin", t[2], op, left, right];
    }
  }
  unary() {
    const t = this.peek();
    if (t[0] === "op") {
      if (["-", "+", "!", "~"].includes(t[1])) {
        this.i++; this.enter();
        try {
          if (t[1] === "-" && ["int", "long", "float", "double"].includes(this.peek()[0]) && !this.isOp(".", 1) && !this.isOp("[", 1)) {
            const [k, v, ln] = this.next(); return this.postfix(["lit", ln, k, k === "long" ? -v : -v]);
          }
          return ["un", t[2], t[1], this.unary()];
        } finally { this.leave(); }
      }
      if (t[1] === "++" || t[1] === "--") { this.i++; const target = this.unary(); if (!["name", "field", "index"].includes(target[0])) throw new PdeError("syntax", `${t[1]} needs a variable`, t[2]); return ["pre", t[2], t[1], target]; }
      if (t[1] === "(") { const cast = this.tryCast(); if (cast !== null) return cast; }
    }
    return this.postfix(this.primary());
  }
  tryCast() {
    const save = this.i, line = this.line; this.i++; const t = this.peek();
    if (t[0] === "id" && (PRIMS.includes(t[1]) || t[1] === "String" || this.classes.has(t[1]) || t[1] === "PVector")) {
      const ty = this.tryType();
      if (ty !== null && this.accept(")")) {
        const nxt = this.peek(), isPrim = typeof ty === "string" && (PRIMS.includes(ty) || ty === "int");
        const ok = ["id", "int", "long", "float", "double", "char", "str", "color"].includes(nxt[0]) || (nxt[0] === "op" && ["(", "!", "~", ...(isPrim ? ["-", "+", "++", "--"] : [])].includes(nxt[1]));
        if (ok && !(nxt[0] === "id" && nxt[1] === "instanceof")) { this.enter(); try { return ["cast", line, ty, this.unary()]; } finally { this.leave(); } }
      }
    }
    this.i = save; return null;
  }
  args() { this.expect("("); const out = []; if (!this.accept(")")) for (;;) { out.push(this.expr()); if (this.accept(")")) break; this.expect(","); } return out; }
  primary() {
    const [k, v, line] = this.next();
    if (["int", "long", "float", "double", "char", "str"].includes(k)) { if (k === "int" && v === 0x80000000) throw new PdeError("syntax", "integer number too large", line); return ["lit", line, k === "str" ? "String" : k, v]; }
    if (k === "color") return ["lit", line, "int", v >= 0x80000000 ? v - 0x100000000 : v];
    if (k === "op") { if (v === "(") { const e = this.expr(); this.expect(")"); return e; } throw new PdeError("syntax", `unexpected ${JSON.stringify(v)}`, line); }
    if (k === "eof") throw new PdeError("syntax", "the sketch ends in the middle of an expression", line);
    if (v === "true" || v === "false") return ["lit", line, "boolean", v === "true"];
    if (v === "null") return ["lit", line, "null", null];
    if (v === "this") return ["this", line];
    if (v === "new") return this.newExpr(line);
    if (PRIMS.includes(v) && this.isOp("(")) return ["call", line, v === "color" ? v : prim(v), this.args()];
    if (PRIMS.includes(v) || v === "String") { if (v === "String" && this.isOp(".")) return ["name", line, v]; throw new PdeError("syntax", `unexpected ${JSON.stringify(v)}`, line); }
    if (KEYWORDS.has(v)) throw new PdeError("syntax", `unexpected ${JSON.stringify(v)}`, line);
    if (this.isOp("(")) return ["call", line, v, this.args()];
    return ["name", line, v];
  }
  newExpr(line) {
    const t = this.peek(); if (t[0] !== "id") throw new PdeError("syntax", "expected a type after new", line);
    let name = t[1]; this.i++;
    if (PRIMS.includes(name) || name === "String" || (this.isOp("[") && name !== "ArrayList")) {
      const base = PRIMS.includes(name) ? prim(name) : name === "String" ? "String" : ["C", name, []]; const dims = []; let extra = 0;
      while (this.isOp("[")) { this.i++; if (this.accept("]")) { extra++; continue; } if (extra) throw new PdeError("syntax", "array sizes must come first", line); dims.push(this.expr()); this.expect("]"); }
      const init = this.isOp("{") ? this.arrayInit() : null; if (!dims.length && init === null) throw new PdeError("syntax", "an array needs a size or {...}", line);
      return ["newarr", line, base, dims, extra, init];
    }
    let args = [];
    if (this.isOp("<")) { const got = this.typeArgs(); args = got === null ? [] : got; }
    while (this.isOp(".") && this.isId(null, 1) && /^[a-z]/.test(name)) { this.i++; name = this.ident(); if (this.isOp("<")) { const got = this.typeArgs(); args = got === null ? [] : got; } }
    return ["new", line, ["C", name, args], this.args()];
  }
  postfix(e) {
    for (;;) {
      const t = this.peek(); if (t[0] !== "op") return e;
      if (t[1] === ".") { this.i++; const name = this.ident(); e = this.isOp("(") ? ["mcall", t[2], e, name, this.args()] : ["field", t[2], e, name]; }
      else if (t[1] === "[") { this.i++; const idx = this.expr(); this.expect("]"); e = ["index", t[2], e, idx]; }
      else if (t[1] === "++" || t[1] === "--") { if (!["name", "field", "index"].includes(e[0])) return e; this.i++; e = ["post", t[2], t[1], e]; }
      else return e;
    }
  }
}

export function parse(src, classes = [], firstLine = 1, bounded = true) { return new Parser(src, classes, firstLine, bounded).program(); }

// ----------------------------------------------------------------------------- bytecode spec
const BC_VERSION = 1;
const BC_OPS = [
  ["NOP", "", 0, 0], ["ICONST", "i", 0, 1], ["FCONST", "f", 0, 1], ["LCONST", "H", 0, 1], ["DCONST", "H", 0, 1],
  ["SCONST", "H", 0, 1], ["NULL", "", 0, 1], ["LOAD", "H", 0, 1], ["STORE", "H", 1, 0], ["GLOAD", "H", 0, 1],
  ["GSTORE", "H", 1, 0], ["DUP", "", 1, 2], ["DUP2", "", 2, 4], ["DUP_X1", "", 2, 3], ["DUP_X2", "", 3, 4],
  ["POP", "", 1, 0], ["SWAP", "", 2, 2],
  ...["IADD", "ISUB", "IMUL", "IDIV", "IREM"].map((n) => [n, "", 2, 1]), ...["INEG"].map((n) => [n, "", 1, 1]),
  ...["IAND", "IOR", "IXOR", "ISHL", "ISHR", "IUSHR"].map((n) => [n, "", 2, 1]), ["INOT", "", 1, 1],
  ...["LADD", "LSUB", "LMUL", "LDIV", "LREM"].map((n) => [n, "", 2, 1]), ["LNEG", "", 1, 1],
  ...["LAND", "LOR", "LXOR", "LSHL", "LSHR", "LUSHR"].map((n) => [n, "", 2, 1]), ["LNOT", "", 1, 1],
  ...["FADD", "FSUB", "FMUL", "FDIV", "FREM"].map((n) => [n, "", 2, 1]), ["FNEG", "", 1, 1],
  ...["DADD", "DSUB", "DMUL", "DDIV", "DREM"].map((n) => [n, "", 2, 1]), ["DNEG", "", 1, 1], ["BNOT", "", 1, 1],
  ...["I2L", "I2F", "I2D", "L2I", "L2F", "L2D", "F2I", "F2L", "F2D", "D2I", "D2L", "D2F", "I2B", "I2S", "I2C"].map((n) => [n, "", 1, 1]),
  ...["ICMP", "LCMP", "FCMP", "DCMP", "ACMP", "SCMP"].map((n) => [n, "B", 2, 1]),
  ["JMP", "H", 0, 0], ["JZ", "H", 1, 0], ["JNZ", "H", 1, 0], ["LOOP", "H", 0, 0],
  ["CALL", "H", 0, 0], ["CALLM", "H", 0, 0], ["RET", "", 0, 0], ["RETV", "", 1, 0],
  ["NEWOBJ", "H", 0, 1], ["GETF", "H", 1, 1], ["PUTF", "H", 2, 0], ["NEWARR", "B", 1, 1],
  ["NEWMULTI", "BB", 0, 1], ["ALOAD", "B", 2, 1], ["ASTORE", "B", 3, 0], ["ALEN", "", 1, 1],
  ["TOSTR", "B", 1, 1], ["SCAT", "", 2, 1], ["NATIVE", "HBB", 0, 0], ["IINC", "Hb", 0, 0],
  ["FAULT", "B", 0, 0], ["PIXELS", "", 0, 1], ["FRAMECOUNT", "", 0, 1], ["NULLCHK", "", 1, 1],
];
const OP = Object.fromEntries(BC_OPS.map((x, i) => [x[0], i]));
const CMP = { "==": 0, "!=": 1, "<": 2, ">": 3, "<=": 4, ">=": 5 };
const KIND = { boolean: 0, byte: 1, short: 2, char: 3, int: 4, long: 5, float: 6, double: 7, ref: 8 };
const TS = { int: 0, long: 1, char: 2, boolean: 3, float: 4, double: 5, String: 6, textfloat: 7 };

const NATIVE_ROWS = [
  ["size", ""], ["smooth", ""], ["no_smooth", ""], ["no_loop", ""], ["loop", ""], ["redraw", ""], ["exit", ""], ["millis", "I"], ["clock", "I"],
  ["background", ""], ["fill", ""], ["stroke", ""], ["color", "I"], ["no_fill", ""], ["no_stroke", ""], ["clear", ""],
  ["stroke_weight", ""], ["stroke_cap", ""], ["stroke_join", ""], ["color_mode", ""], ["red", "F"], ["green", "F"],
  ["blue", "F"], ["alpha", "F"], ["brightness", "F"], ["hue", "F"], ["saturation", "F"], ["lerp_color", "I"],
  ["texture", ""], ["stroke_texture", ""], ["no_texture", ""], ["no_stroke_texture", ""], ["texture_seed", ""], ["background_texture", ""],
  ["point", ""], ["line", ""], ["rect", ""], ["square", ""], ["ellipse", ""], ["circle", ""], ["arc", ""], ["triangle", ""],
  ["quad", ""], ["bezier", ""], ["curve", ""], ["begin_shape", ""], ["end_shape", ""], ["vertex", ""], ["bezier_vertex", ""],
  ["quadratic_vertex", ""], ["curve_vertex", ""], ["begin_contour", ""], ["end_contour", ""], ["rect_mode", ""], ["ellipse_mode", ""],
  ["curve_tightness", ""], ["bezier_point", "F"], ["bezier_tangent", "F"], ["curve_point", "F"],
  ["push_matrix", ""], ["pop_matrix", ""], ["push", ""], ["pop", ""], ["push_style", ""], ["pop_style", ""], ["translate", ""],
  ["rotate", ""], ["scale", ""], ["shear_x", ""], ["shear_y", ""], ["reset_matrix", ""], ["screen_x", "F"], ["screen_y", "F"],
  ["text", ""], ["text_box", ""], ["text_size", ""], ["text_align", ""], ["text_leading", ""], ["text_width", "F"],
  ["text_ascent", "F"], ["text_descent", "F"], ["create_font", "R"], ["text_font", ""],
  ["load_pixels", ""], ["update_pixels", ""], ["get", "I"], ["set", ""],
  ["random", "F"], ["random2", "F"], ["random_seed", ""], ["random_gaussian", "F"], ["noise", "F"], ["noise_seed", ""], ["noise_detail", ""],
  ["sin", "F"], ["cos", "F"], ["tan", "F"], ["asin", "F"], ["acos", "F"], ["atan", "F"], ["sqrt", "F"], ["exp", "F"],
  ["log", "F"], ["degrees", "F"], ["radians", "F"], ["atan2", "F"], ["pow", "F"], ["absi", "I"], ["absf", "F"],
  ["mini", "I"], ["minf", "F"], ["maxi", "I"], ["maxf", "F"], ["min_arri", "I"], ["min_arrf", "F"], ["max_arri", "I"], ["max_arrf", "F"],
  ["constraini", "I"], ["constrainf", "F"], ["map", "F"], ["lerp", "F"], ["norm", "F"], ["dist", "F"], ["mag", "F"],
  ["sq", "F"], ["floor", "I"], ["ceil", "I"], ["round", "I"],
  ["m_sin", "D"], ["m_cos", "D"], ["m_tan", "D"], ["m_asin", "D"], ["m_acos", "D"], ["m_atan", "D"], ["m_sqrt", "D"],
  ["m_exp", "D"], ["m_log", "D"], ["m_floor", "D"], ["m_ceil", "D"], ["m_toradians", "D"], ["m_todegrees", "D"],
  ["m_cbrt", "D"], ["m_log10", "D"], ["m_signum", "D"], ["m_rint", "D"], ["m_pow", "D"], ["m_atan2", "D"],
  ["m_hypot", "D"], ["m_roundd", "L"], ["m_random", "D"], ["m_absl", "L"], ["m_absd", "D"], ["m_minl", "L"],
  ["m_maxl", "L"], ["m_mind", "D"], ["m_maxd", "D"],
  ["parse_int", "I"], ["parse_float", "F"], ["parse_bool", "I"], ["nf", "R"], ["hex", "R"], ["unhex", "I"],
  ["s_length", "I"], ["s_charat", "I"], ["s_substring", "R"], ["s_indexof", "I"], ["s_lastindexof", "I"], ["s_upper", "R"],
  ["s_lower", "R"], ["s_trim", "R"], ["s_isempty", "I"], ["s_equals", "I"], ["s_equalsic", "I"], ["s_startswith", "I"],
  ["s_endswith", "I"], ["s_contains", "I"], ["s_compareto", "I"], ["s_replace", "R"], ["s_hashcode", "I"], ["s_repeat", "R"],
  ["s_fromchars", "R"], ["s_tochars", "R"], ["ptrim", "R"], ["char_test", "I"],
  ["a_append", "R"], ["a_shorten", "R"], ["a_expand", "R"], ["a_subset", "R"], ["a_concat", "R"], ["a_reverse", "R"],
  ["a_sort", "R"], ["a_copy", ""], ["a_clone", "R"], ["a_toint", "R"],
  ["rotate_x", ""], ["rotate_y", ""], ["rotate_axis", ""], ["apply_matrix", ""], ["camera", ""], ["perspective", ""],
  ["ortho", ""], ["frustum", ""], ["screen_z", "F"], ["lights", ""], ["no_lights", ""], ["ambient_light", ""],
  ["directional_light", ""], ["point_light", ""], ["spot_light", ""], ["light_falloff", ""], ["light_specular", ""],
  ["ambient", ""], ["specular", ""], ["emissive", ""], ["shininess", ""], ["normal", ""], ["hint", ""], ["box", ""],
  ["sphere_detail", ""], ["sphere", ""], ["text_z", ""], ["blend_mode", ""], ["filter", ""],
];
const NATIVE = Object.fromEntries(NATIVE_ROWS.map((x, i) => [x[0], i]));

// Processing constants. Float constants are written as their already-rounded IEEE-754 values.
const CONST = {
  PI: ["float", 3.1415927410125732], HALF_PI: ["float", 1.5707963705062866], QUARTER_PI: ["float", 0.7853981852531433],
  TWO_PI: ["float", 6.2831854820251465], TAU: ["float", 6.2831854820251465], DEG_TO_RAD: ["float", 0.01745329238474369],
  RAD_TO_DEG: ["float", 57.2957763671875], CORNER: ["int", 0], CORNERS: ["int", 1], RADIUS: ["int", 2], CENTER: ["int", 3],
  DIAMETER: ["int", 3], BASELINE: ["int", 0], TOP: ["int", 101], BOTTOM: ["int", 102], LEFT: ["int", 37], UP: ["int", 38],
  RIGHT: ["int", 39], DOWN: ["int", 40], OPEN: ["int", 1], CLOSE: ["int", 2], CHORD: ["int", 2], PIE: ["int", 3],
  SQUARE: ["int", 1], ROUND: ["int", 2], PROJECT: ["int", 4], MITER: ["int", 8], BEVEL: ["int", 32], RGB: ["int", 1],
  ARGB: ["int", 2], HSB: ["int", 3], ALPHA: ["int", 4], POINTS: ["int", 3], LINES: ["int", 5], TRIANGLES: ["int", 9],
  TRIANGLE_STRIP: ["int", 10], TRIANGLE_FAN: ["int", 11], QUADS: ["int", 17], QUAD_STRIP: ["int", 18], POLYGON: ["int", 20],
  REPLACE: ["int", 0], BLEND: ["int", 1], ADD: ["int", 2], SUBTRACT: ["int", 4], LIGHTEST: ["int", 8], DARKEST: ["int", 16],
  DIFFERENCE: ["int", 32], EXCLUSION: ["int", 64], MULTIPLY: ["int", 128], SCREEN: ["int", 256],
  JAVA2D: ["String", "processing.awt.PGraphicsJava2D"], P2D: ["String", "processing.opengl.PGraphics2D"],
  P3D: ["String", "processing.opengl.PGraphics3D"], THRESHOLD: ["int", 16], GRAY: ["int", 12], INVERT: ["int", 13],
  POSTERIZE: ["int", 15], BLUR: ["int", 11], ERODE: ["int", 17], DILATE: ["int", 18], OPAQUE: ["int", 14],
  DISABLE_DEPTH_TEST: ["int", 2], ENABLE_DEPTH_TEST: ["int", -2], ENABLE_DEPTH_SORT: ["int", 3], DISABLE_DEPTH_SORT: ["int", -3],
  DISABLE_DEPTH_MASK: ["int", 5], ENABLE_DEPTH_MASK: ["int", -5], DISABLE_OPTIMIZED_STROKE: ["int", 6],
  ENABLE_OPTIMIZED_STROKE: ["int", -6], ENABLE_STROKE_PERSPECTIVE: ["int", 7], DISABLE_STROKE_PERSPECTIVE: ["int", -7],
  LINE_STRIP: ["int", 50], LINE_LOOP: ["int", 51],
};

// ----------------------------------------------------------------------------- static types and constants
const INTS = ["int", "long", "short", "byte", "char"];
const FLOATS = ["float", "double"];
const NUMS = [...INTS, ...FLOATS];
const RANK = { byte: 1, short: 2, char: 2, int: 3, long: 4, float: 5, double: 6 };
const BOX = { Integer: "int", Float: "float", Double: "double", Long: "long", Boolean: "boolean", Character: "char", Short: "short", Byte: "byte" };
const teq = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => teq(v, b[i])));
function tname(t) { return Array.isArray(t) ? t[0] === "[]" ? `${tname(t[1])}[]` : `${t[1]}${t[2].length ? `<${t[2].map(tname).join(", ")}>` : ""}` : t; }
function norm(t) {
  if (Array.isArray(t) && t[0] === "C" && Object.hasOwn(BOX, t[1])) return BOX[t[1]];
  if (Array.isArray(t) && t[0] === "C" && t[1] === "String") return "String";
  if (Array.isArray(t) && t[0] === "[]") return ["[]", norm(t[1])];
  if (Array.isArray(t) && t[0] === "C" && t[2].length) return ["C", t[1], t[2].map(norm)];
  return t;
}
const isRef = (t) => t === "String" || t === "null" || Array.isArray(t);
const kindOf = (t) => typeof t === "string" && Object.hasOwn(KIND, t) && t !== "ref" ? KIND[t] : KIND.ref;
const primSlot = (t) => ["int", "short", "byte", "char", "boolean"].includes(t) ? "I" : t === "long" ? "L" : t === "float" ? "F" : t === "double" ? "D" : "R";
const defaultConst = (t) => INTS.includes(t) ? (t === "long" ? 0n : 0) : FLOATS.includes(t) ? 0 : t === "boolean" ? false : null;
const keyOf = (v) => typeof v === "bigint" ? `b:${v}` : typeof v === "number" ? `n:${Object.is(v, -0) ? "-0" : String(v)}` : `${typeof v}:${String(v)}`;

class Label { constructor() { this.pos = null; } }
class X { constructor(t, code, value = NOCONST) { this.t = t; this.code = code; this.const = value; } }
class Func {
  constructor(name, params, ret, node, cls = null, kind = "func") {
    Object.assign(this, { name, params, ret, node, cls, kind, index: -1, nslots: 2 + params.length, code: [], hasThis: cls !== null });
  }
}
class ClassB { constructor(name) { Object.assign(this, { name, fields: new Map(), inits: [], methods: new Map(), ctors: [], index: -1, news: new Map() }); } }

function constConv(v, src, to) {
  if (to === "double") return Number(v);
  if (to === "float") return f32(Number(v));
  if (FLOATS.includes(src)) v = to === "long" ? d2l(v) : d2i(v);
  if (to === "int") return wrap32(v);
  if (to === "long") return wrap64(v);
  if (to === "short") return Number(BigInt.asIntN(16, BigInt(v)));
  if (to === "byte") return Number(BigInt.asIntN(8, BigInt(v)));
  if (to === "char") return Number(BigInt.asUintN(16, BigInt(v)));
  return v;
}
function d2i(v) { if (Number.isNaN(v)) return 0; if (v >= 2147483647) return 2147483647; if (v <= -2147483648) return -2147483648; return Math.trunc(v); }
function d2l(v) { if (Number.isNaN(v)) return 0n; if (v >= 9.223372036854776e18) return 9223372036854775807n; if (v <= -9.223372036854776e18) return -9223372036854775808n; return BigInt(Math.trunc(v)); }
function idiv(a, b) { if (b === 0 || b === 0n) throw new PdeError("runtime", "division by zero"); return typeof a === "bigint" ? a / b : Math.trunc(a / b); }
function imod(a, b) { if (b === 0 || b === 0n) throw new PdeError("runtime", "division by zero"); return a % b; }
function pyFloatMod(a, b) { return b === 0 ? NaN : a % b; }
function jfloatStr(v, single = true) {
  if (Number.isNaN(v)) return "NaN"; if (!Number.isFinite(v)) return v > 0 ? "Infinity" : "-Infinity";
  if (v === 0) return Object.is(v, -0) ? "-0.0" : "0.0";
  const top = single ? 9 : 17; let p = 0, s;
  while (p < top) { s = v.toExponential(p); const back = Number(s); if ((single ? f32(back) : back) === v) break; p++; }
  s = v.toExponential(p); let [mant, exp0] = s.split("e"), neg = mant.startsWith("-");
  let digits = mant.replace("-", "").replace(".", "").replace(/0+$/, "") || "0"; const exp = Number(exp0), sign = neg ? "-" : "";
  if (Math.abs(v) >= 1e-3 && Math.abs(v) < 1e7) {
    const ip = exp >= 0 ? digits.slice(0, exp + 1).padEnd(exp + 1, "0") : "0";
    const fp = exp >= 0 ? digits.slice(exp + 1) || "0" : "0".repeat(-exp - 1) + digits;
    return `${sign}${ip}.${fp}`;
  }
  return `${sign}${digits[0]}.${digits.slice(1) || "0"}E${exp}`;
}
function jstr(v, t) {
  if (t === "String") return v === null ? "null" : v;
  if (t === "boolean") return v ? "true" : "false";
  if (t === "char") return String.fromCharCode(v & 0xffff);
  if (["int", "long", "short", "byte"].includes(t)) return String(v);
  if (t === "float") return jfloatStr(v, true);
  if (t === "double") return jfloatStr(v, false);
  if (v === null) return "null";
  return `${tname(t)}@0`;
}

// ----------------------------------------------------------------------------- bytecode compiler
class BCompiler {
  constructor() {
    this.funcs = new Map(); this.classes = new Map(); this.classNodes = new Map(); this.gtypes = new Map(); this.allFuncs = [];
    this.scopes = []; this.nslots = 2; this.maxslots = 2; this.cls = null; this.retT = "void"; this.loops = []; this.fnEnd = null;
    this.inGlobalInit = false; this.longs = new Map(); this.doubles = new Map(); this.strings = new Map(); this.usesClock = false;
    this.p3d = false; this.entry = { globals: -1, static: -1, setup: -1, draw: -1 };
  }
  err(message, line, kind = "compile") { throw new PdeError(kind, message, line); }
  unsup(what, line = 0) { throw new Unsupported(what, line); }
  addFunc(f) { f.index = this.allFuncs.length; if (f.index >= MAX_FUNCS) this.unsup("too many functions"); this.allFuncs.push(f); return f; }
  pool(table, key, value) {
    const k = keyOf(key); if (!table.has(k)) table.set(k, [table.size, value]); if (table.size > 65535) this.unsup("too many constants"); return table.get(k)[0];
  }
  pushConst(v, t) {
    if (["int", "short", "byte", "char"].includes(t)) return [["ICONST", Number(v)]];
    if (t === "boolean") return [["ICONST", v ? 1 : 0]];
    if (t === "long") return [["LCONST", this.pool(this.longs, BigInt(v), BigInt(v))]];
    if (t === "float") return [["FCONST", Number(v)]];
    if (t === "double") { const n = Number(v), b = new ArrayBuffer(8); new DataView(b).setFloat64(0, n, true); const key = [...new Uint8Array(b)].join(","); return [["DCONST", this.pool(this.doubles, key, n)]]; }
    if (t === "String") { if (v === null) return [["NULL"]]; this.checkStr(v, 0); return [["SCONST", this.pool(this.strings, v, v)]]; }
    if (t === "null" || v === null) return [["NULL"]];
    this.unsup(`a constant of type ${tname(t)}`);
  }
  checkStr(v, line) { for (const ch of v) if (ch.codePointAt(0) > 0xffff) this.unsup("a character outside the basic plane", line); }
  cx(v, t) { if (t === "float") v = f32(v); return new X(t, this.pushConst(v, t), v); }

  compile(items) {
    const globals = [], statics = [];
    for (const it of items) if (it[0] === "class") { if (this.classes.has(it[2])) this.err(`class ${it[2]} is declared twice`, it[1]); this.classes.set(it[2], new ClassB(it[2])); this.classNodes.set(it[2], it[3]); }
    let ciIndex = 0; for (const ci of this.classes.values()) ci.index = ciIndex++;
    for (const it of items) {
      const k = it[0];
      if (k === "class") this.declareClass(it);
      else if (k === "func") {
        const [, line, ret, name, params] = it;
        const f = this.addFunc(new Func(name, params.map((p) => this.checkType(norm(p[0]), line)), this.checkType(norm(ret), line), it));
        if (!this.funcs.has(name)) this.funcs.set(name, []); this.funcs.get(name).push(f);
      } else if (k === "global") {
        globals.push(it);
        for (const [t0, name] of it[2][2]) { const t = this.checkType(norm(t0), it[1]); if (this.gtypes.has(name)) this.err(`${name} is declared twice`, it[1]); this.gtypes.set(name, [this.gtypes.size, t]); }
      } else if (k === "stmt") statics.push(it[2]);
    }
    for (const ci of this.classes.values()) this.compileClass(ci);
    const gf = this.addFunc(new Func("<globals>", [], "void", null, null, "globals"));
    this.inGlobalInit = true; this.beginFunction(); this.fnEnd = new Label(); let code = [];
    for (const it of globals) for (const [, name, init] of it[2][2]) if (init !== null) {
      const [slot, gt] = this.gtypes.get(name), x = this.storeConv(this.valueFor(init, gt, it[1]), gt, it[1]); code.push(...x.code, ["GSTORE", slot]);
    }
    code.push(["L", this.fnEnd], ["RET"]); gf.code = code; gf.nslots = this.maxslots; this.inGlobalInit = false; this.entry.globals = gf.index;
    for (const sigs of [...this.funcs.values()]) for (const f of sigs) this.compileFunc(f);
    for (const name of ["setup", "draw"]) for (const f of this.funcs.get(name) || []) if (!f.params.length) this.entry[name] = f.index;
    if (statics.length) {
      const sf = this.addFunc(new Func("<static>", [], "void", null, null, "static")); this.beginFunction(); this.retT = "void"; this.fnEnd = new Label();
      sf.code = [...this.blockOf(statics, 0), ["L", this.fnEnd], ["RET"]]; sf.nslots = this.maxslots; this.entry.static = sf.index;
    }
  }
  checkType(t, line) {
    if (Array.isArray(t)) {
      if (t[0] === "[]") this.checkType(t[1], line);
      else if (!this.classes.has(t[1]) && !["PVector", "ArrayList", "PFont", "Object", "List", "PImage", "PGraphics", "IntList", "FloatList", "StringList", "HashMap", "Map"].includes(t[1])) this.err(`the type ${t[1]} is not available here`, line);
      else if (["PVector", "ArrayList", "List", "Object", "PImage", "PGraphics", "IntList", "FloatList", "StringList", "HashMap", "Map"].includes(t[1])) this.unsup(`${t[1]} on the VM`, line);
    }
    return t;
  }
  beginFunction(params = []) { this.scopes = [new Map()]; this.nslots = 2; this.maxslots = 2; this.loops = []; for (const [t, name] of params) this.declareLocal(name, t, 0); }
  declareLocal(name, t, line) { const sc = this.scopes.at(-1); if (sc.has(name)) this.err(`${name} is already defined here`, line); const slot = this.nslots++; this.maxslots = Math.max(this.maxslots, this.nslots); sc.set(name, [slot, t]); return slot; }
  temp() { return this.declareLocal(`<t${this.nslots}>`, "int", 0); }
  pushScope() { this.scopes.push(new Map()); }
  popScope() { const sc = this.scopes.pop(); this.nslots -= sc.size; }
  findLocal(name) { for (let i = this.scopes.length - 1; i >= 0; i--) if (this.scopes[i].has(name)) return this.scopes[i].get(name); return null; }

  declareClass(it) {
    const [, , name, members] = it, ci = this.classes.get(name);
    for (const m of members) {
      const k = m[0];
      if (k === "global") for (const [t0, fname] of m[2][2]) ci.fields.set(fname, [ci.fields.size, this.checkType(norm(t0), m[1])]);
      else if (k === "func") {
        const [, line, ret, mname, params] = m, f = this.addFunc(new Func(mname, params.map((p) => this.checkType(norm(p[0]), line)), this.checkType(norm(ret), line), m, ci));
        if (!ci.methods.has(mname)) ci.methods.set(mname, []); ci.methods.get(mname).push(f);
      } else if (k === "ctor") {
        const [, line, , params, body] = m;
        ci.ctors.push(this.addFunc(new Func(name, params.map((p) => this.checkType(norm(p[0]), line)), "void", ["func", line, "void", name, params, body], ci)));
      } else if (k === "class") this.err("a class inside a class is not available here", m[1]);
    }
  }
  compileClass(ci) {
    this.cls = ci; this.beginFunction(); const inits = [];
    for (const it of (this.classNodes.get(ci.name) || []).filter((m) => m[0] === "global")) for (const [, fname, init] of it[2][2]) if (init !== null) {
      const [idx, ft] = ci.fields.get(fname); inits.push([idx, this.storeConv(this.valueFor(init, ft, it[1]), ft, it[1])]);
    }
    const initSlots = this.maxslots;
    for (const ctor of ci.ctors.length ? ci.ctors : [null]) {
      const params = ctor ? ctor.params : [], nf = this.addFunc(new Func(`<new ${ci.name}>`, params, ["C", ci.name, []], null, null, "new"));
      const n = params.length, obj = 2 + n; let code = [["NEWOBJ", ci.index], ["STORE", obj]];
      for (const [idx, x] of inits) code.push(["LOAD", obj], ...this.relocate(x.code, obj + 1), ["PUTF", idx]);
      if (ctor) { for (let i = 0; i < n; i++) code.push(["LOAD", 2 + i]); code.push(["LOAD", obj], ["CALLM", ctor]); }
      code.push(["LOAD", obj], ["RETV"]); nf.code = code; nf.nslots = obj + 1 + Math.max(0, initSlots - 2); ci.news.set(ctor, nf);
    }
    for (const sigs of ci.methods.values()) for (const f of sigs) this.compileFunc(f); for (const f of ci.ctors) this.compileFunc(f); this.cls = null;
  }
  relocate(code, base) { return code.map((ins) => { if (!["LOAD", "STORE", "IINC"].includes(ins[0])) return ins; let s = ins[1]; if (s === 1) s = base - 1; else if (s >= 2) s = base + s - 2; return [ins[0], s, ...ins.slice(2)]; }); }
  compileFunc(f) {
    const [, , , , params, body] = f.node; this.cls = f.cls; this.retT = f.ret; this.beginFunction(f.params.map((t, i) => [t, params[i][1]])); this.fnEnd = new Label();
    const tail = [["L", this.fnEnd], ...(f.ret === "void" ? [["RET"]] : [...this.defaultCode(f.ret), ["RETV"]])];
    f.code = [...this.stmt(body), ...tail]; f.nslots = this.maxslots; this.cls = null;
  }
  defaultCode(t) {
    // Python constructs this selection as a dict literal, so its long and double entries are
    // evaluated (and interned) even when another slot is selected. Preserve that observable pool
    // ordering: byte identity includes constants that are not ultimately referenced.
    const choices = { I: [["ICONST", 0]], L: this.pushConst(0n, "long"), F: [["FCONST", 0]],
                      D: this.pushConst(0, "double"), R: [["NULL"]] };
    return choices[primSlot(t)];
  }

  blockOf(stmts) { this.pushScope(); const code = []; for (const s of stmts) code.push(...this.stmt(s)); this.popScope(); return code; }
  stmt(s) { const m = this[`s_${s[0]}`]; if (!m) this.err(`${s[0]} is not available here`, s[1]); return m.call(this, s); }
  s_block(s) { return this.blockOf(s[2]); }
  s_empty() { return []; }
  s_expr(s) {
    const x = this.expr(s[2]), code = [...x.code]; if (x.t === "void") return code;
    if (code.length >= 2 && code.at(-2)[0] === "DUP" && ["STORE", "GSTORE"].includes(code.at(-1)[0])) return [...code.slice(0, -2), code.at(-1)];
    if (code.length >= 2 && code.at(-2)[0] === "DUP_X1" && code.at(-1)[0] === "PUTF") return [...code.slice(0, -2), code.at(-1)];
    if (code.length >= 2 && code.at(-2)[0] === "DUP_X2" && code.at(-1)[0] === "ASTORE") return [...code.slice(0, -2), code.at(-1)];
    return [...code, ["POP"]];
  }
  s_vardecl(s) {
    const code = [];
    for (const [t0, name, init] of s[2]) { const t = this.checkType(norm(t0), s[1]), x = init !== null ? this.storeConv(this.valueFor(init, t, s[1]), t, s[1]) : null, slot = this.declareLocal(name, t, s[1]); code.push(...(x ? x.code : this.defaultCode(t)), ["STORE", slot]); }
    return code;
  }
  s_if(s) { const c = this.cond(s[2]), then = this.stmt(s[3]), els = new Label(), end = new Label(); return s[4] === null ? [...c, ["JZ", end], ...then, ["L", end]] : [...c, ["JZ", els], ...then, ["JMP", end], ["L", els], ...this.stmt(s[4]), ["L", end]]; }
  loop(body, brk, cnt) { this.loops.push([brk, cnt]); const code = this.stmt(body); this.loops.pop(); return code; }
  s_while(s) { const top = new Label(), brk = new Label(), cnt = new Label(); return [["L", top], ...this.cond(s[2]), ["JZ", brk], ...this.loop(s[3], brk, cnt), ["L", cnt], ["LOOP", top], ["L", brk]]; }
  s_do(s) { const top = new Label(), brk = new Label(), cnt = new Label(); return [["L", top], ...this.loop(s[2], brk, cnt), ["L", cnt], ...this.cond(s[3]), ["JZ", brk], ["LOOP", top], ["L", brk]]; }
  s_for(s) {
    const [, line, init, cond, update, body] = s; this.pushScope(); const code = []; for (const i of init) code.push(...this.stmt(i)); const c = cond === null ? null : this.cond(cond), ups = [];
    for (const u of update) ups.push(...this.s_expr(["expr", line, u])); const top = new Label(), brk = new Label(), cnt = new Label(), b = this.loop(body, brk, cnt); this.popScope();
    code.push(["L", top]); if (c) code.push(...c, ["JZ", brk]); code.push(...b, ["L", cnt], ...ups, ["LOOP", top], ["L", brk]); return code;
  }
  s_foreach(s) {
    const [, line, t0, name, it, body] = s, src = this.expr(it), t = this.checkType(norm(t0), line), st = src.t; let et;
    if (Array.isArray(st) && st[0] === "[]") et = st[1]; else if (Array.isArray(st) && st[0] === "C" && ["ArrayList", "List"].includes(st[1])) this.unsup("ArrayList on the VM", line); else this.err(`cannot loop over a ${tname(st)}`, line);
    const conv = this.storeConv(new X(et, []), t, line).code; this.pushScope(); const arr = this.temp(), idx = this.temp(), slot = this.declareLocal(name, t, line), top = new Label(), brk = new Label(), cnt = new Label(), b = this.loop(body, brk, cnt); this.popScope();
    return [...src.code, ["NULLCHK"], ["STORE", arr], ["ICONST", 0], ["STORE", idx], ["L", top], ["LOAD", idx], ["LOAD", arr], ["ALEN"], ["ICMP", CMP["<"]], ["JZ", brk], ["LOAD", arr], ["LOAD", idx], ["ALOAD", kindOf(et)], ...conv, ["STORE", slot], ...b, ["L", cnt], ["IINC", idx, 1], ["LOOP", top], ["L", brk]];
  }
  s_switch(s) {
    const [, line, e, cases] = s, se = this.expr(e); if (!INTS.includes(se.t) && se.t !== "String") this.err("switch needs an int, a char or a String", line);
    const table = new Map(); let defaultAt = null; this.pushScope(); const tmp = this.temp(), brk = new Label(); let innerCnt = null;
    for (let i = this.loops.length - 1; i >= 0; i--) if (this.loops[i][1] !== null) { innerCnt = this.loops[i][1]; break; }
    this.loops.push([brk, innerCnt]); const labels = [], bodies = [];
    for (const [labs, body] of cases) { const lab = new Label(), idx = labels.length; labels.push(lab); if (labs === null) defaultAt = idx; else for (const lb of labs) { const le = this.expr(lb); if (le.const === NOCONST) this.err("a case label must be a constant", line); table.set(keyOf(le.const), [le.const, idx]); } const code = []; for (const b of body) code.push(...this.stmt(b)); bodies.push(code); }
    this.loops.pop(); this.popScope(); const out = [...se.code, ["STORE", tmp]];
    for (const [k, idx] of table.values()) {
      if (se.t === "String") { if (typeof k !== "string") continue; out.push(["LOAD", tmp], ...this.pushConst(k, "String"), ["SCMP", CMP["=="]], ["JNZ", labels[idx]]); }
      else { if (typeof k === "string" || typeof k === "number" && !Number.isInteger(k)) { if (typeof k === "string") continue; this.unsup("a non-integer case label", line); } if (se.t === "long") out.push(["LOAD", tmp], ...this.pushConst(BigInt(k), "long"), ["LCMP", CMP["=="]], ["JNZ", labels[idx]]); else if (Number(k) >= -(2 ** 31) && Number(k) < 2 ** 31) out.push(["LOAD", tmp], ["ICONST", Number(k)], ["ICMP", CMP["=="]], ["JNZ", labels[idx]]); }
    }
    out.push(["JMP", defaultAt !== null ? labels[defaultAt] : brk]); for (let i = 0; i < labels.length; i++) out.push(["L", labels[i]], ...bodies[i]); out.push(["L", brk]); return out;
  }
  s_break(s) { if (s[2] !== null) this.err("labelled break is not available here", s[1]); if (!this.loops.length) this.err("break outside a loop or switch", s[1]); return [["JMP", this.loops.at(-1)[0]]]; }
  s_continue(s) { if (s[2] !== null) this.err("labelled continue is not available here", s[1]); if (!this.loops.length) this.err("continue outside a loop", s[1]); return [["JMP", this.loops.at(-1)[1] || this.fnEnd]]; }
  s_labeled(s) { return this.stmt(s[3]); }
  s_return(s) { if (s[2] === null) return this.retT !== "void" ? [...this.defaultCode(this.retT), ["RETV"]] : [["RET"]]; if (this.retT === "void") this.err("a void function cannot return a value", s[1]); return [...this.storeConv(this.valueFor(s[2], this.retT, s[1]), this.retT, s[1]).code, ["RETV"]]; }

  valueFor(node, t, line) { if (node[0] === "arrinit") { if (!(Array.isArray(t) && t[0] === "[]")) this.err("{...} needs an array type", line); return this.arrayInit(node, t); } return this.expr(node); }
  conv(x, to, line, explicit = false) {
    const src = x.t; if (teq(src, to)) return x;
    if (x.const !== NOCONST && [...NUMS, "boolean"].includes(src) && NUMS.includes(to)) {
      if (src === "boolean") this.err(`cannot convert boolean to ${to}`, line); return this.cx(constConv(x.const, src, to), to);
    }
    const c = [...x.code];
    if (NUMS.includes(to) && NUMS.includes(src)) return new X(to, [...c, ...numConv(src, to).map((o) => [o])]);
    if (to === "String") return src === "null" ? new X(to, c) : new X(to, [...c, ...this.toStr(src, line)]);
    if (src === "null" && isRef(to)) return new X(to, c);
    if (isRef(to) && isRef(src)) {
      if (Array.isArray(to) && Array.isArray(src)) {
        const bad = to[0] !== src[0] || (to[0] === "C" && !["Object", "List"].includes(to[1]) && src[1] !== to[1] && !(["ArrayList", "List"].includes(to[1]) && ["ArrayList", "List"].includes(src[1])) && !(["HashMap", "Map"].includes(to[1]) && ["HashMap", "Map"].includes(src[1])) && !(to[1] === "PImage" && src[1] === "PGraphics"));
        if (bad && !(to[0] === "[]" && src[0] === "[]")) this.err(`cannot convert ${tname(src)} to ${tname(to)}`, line);
      } else if (to !== "Object" && !teq(src, to) && !(Array.isArray(to) && to[1] === "Object")) this.err(`cannot convert ${tname(src)} to ${tname(to)}`, line);
      return new X(to, c);
    }
    if (Array.isArray(src) && src[0] === "C" && src[1] === "Object" && [...NUMS, "boolean"].includes(to)) this.unsup("Object on the VM", line);
    this.err(`cannot convert ${tname(src)} to ${tname(to)}`, line);
  }
  toStr(t, line) { if (["int", "short", "byte"].includes(t)) return [["TOSTR", TS.int]]; if (["long", "char", "boolean", "float", "double", "String"].includes(t)) return [["TOSTR", TS[t]]]; if (t === "null") return [["POP"], ...this.pushConst("null", "String")]; this.unsup(`turning a ${tname(t)} into text`, line); }
  storeConv(x, t, line) { return teq(x.t, t) ? x : NUMS.includes(x.t) && NUMS.includes(t) ? this.conv(x, t, line, true) : this.conv(x, t, line); }
  cond(node) { const x = this.expr(node); if (x.t !== "boolean") this.err(`a condition must be true/false, not ${tname(x.t)}`, node[1]); return x.code; }
  expr(n) { const m = this[`e_${n[0]}`]; if (!m) this.err(`${n[0]} is not available here`, n[1]); return m.call(this, n); }
  e_lit(n) { const [, line, t, v] = n; if (t === "String") { if (v.length > 65536) this.err("the string is too long", line); this.checkStr(v, line); } return this.cx(v, t); }
  e_this(n) { if (this.cls === null) this.err("this is only available inside a class", n[1]); return new X(["C", this.cls.name, []], [["LOAD", 1]]); }
  e_name(n) {
    const [, line, name] = n, loc = this.findLocal(name); if (loc) return new X(loc[1], [["LOAD", loc[0]]]);
    if (this.cls && this.cls.fields.has(name)) { const [idx, t] = this.cls.fields.get(name); return new X(t, [["LOAD", 1], ["GETF", idx]]); }
    if (this.gtypes.has(name)) { const [slot, t] = this.gtypes.get(name); return new X(t, [["GLOAD", slot]]); }
    return this.builtinVar(name, line);
  }
  builtinVar(name, line) {
    if (Object.hasOwn(CONST, name)) return this.cx(CONST[name][1], CONST[name][0]);
    if (["width", "height"].includes(name)) return this.cx(this.inGlobalInit ? 100 : name === "width" ? 240 : 128, "int");
    if (name === "frameCount") return new X("int", [["FRAMECOUNT"]]); if (name === "frameRate") return this.cx(60, "float");
    if (["mouseX", "mouseY", "pmouseX", "pmouseY", "keyCode"].includes(name)) return this.cx(0, "int");
    if (["mousePressed", "keyPressed", "focused"].includes(name)) return this.cx(false, "boolean"); if (name === "key") return this.cx(0, "char");
    if (["displayWidth", "displayHeight"].includes(name)) return this.cx(name === "displayWidth" ? 240 : 128, "int"); if (name === "pixels") return new X(["[]", "int"], [["PIXELS"]]);
    if (["Math", "PVector", "String", "Integer", "Float", "Double", "Long", "Character", "Boolean", "PApplet", "System"].includes(name)) return new X(["static", name], []);
    this.err(`${name} is not defined`, line);
  }
  e_un(n) {
    const [, line, op, a] = n; let x = this.expr(a);
    if (op === "!") { if (x.t !== "boolean") this.err("! needs true/false", line); return x.const !== NOCONST ? this.cx(!x.const, "boolean") : new X("boolean", [...x.code, ["BNOT"]]); }
    if (!NUMS.includes(x.t)) this.err(`${op} needs a number`, line); const t = RANK[x.t] >= 3 ? x.t : "int";
    if (op === "+") return t !== x.t ? this.conv(x, t, line) : x;
    if (op === "~") { if (!["int", "long"].includes(t)) this.err("~ needs an integer", line); if (t !== x.t) x = this.conv(x, t, line); return new X(t, [...x.code, [t === "int" ? "INOT" : "LNOT"]]); }
    if (t !== x.t) x = this.conv(x, t, line); if (x.const !== NOCONST) { const v = typeof x.const === "bigint" ? -x.const : -x.const; return this.cx(t === "int" ? wrap32(v) : t === "long" ? wrap64(v) : v, t); }
    return new X(t, [...x.code, [{ int: "INEG", long: "LNEG", float: "FNEG", double: "DNEG" }[t]]]);
  }
  promote(a, b, line, op) {
    if (!NUMS.includes(a.t) || !NUMS.includes(b.t)) this.err(`${op} needs numbers, not ${tname(a.t)} and ${tname(b.t)}`, line);
    const t = [a.t, b.t].includes("double") ? "double" : [a.t, b.t].includes("float") ? "float" : [a.t, b.t].includes("long") ? "long" : "int";
    if (RANK[a.t] < 3 && a.t !== t) a = this.conv(a, "int", line); if (RANK[b.t] < 3 && b.t !== t) b = this.conv(b, "int", line);
    return [a.t !== t ? this.conv(a, t, line) : a, b.t !== t ? this.conv(b, t, line) : b, t];
  }
  e_bin(n) {
    const [, line, op, an, bn] = n;
    if (["&&", "||"].includes(op)) { const a = this.expr(an), b = this.expr(bn); if (a.t !== "boolean" || b.t !== "boolean") this.err(`${op} needs true/false on both sides`, line); const end = new Label(); return new X("boolean", [...a.code, ["DUP"], [op === "&&" ? "JZ" : "JNZ", end], ["POP"], ...b.code, ["L", end]]); }
    return this.binop(op, this.expr(an), this.expr(bn), line);
  }
  fold(op, a, b, line) {
    if (a.const === NOCONST || b.const === NOCONST) return null;
    try {
      let av = a.const, bv = b.const, v, t;
      if (op === "+" && (a.t === "String" || b.t === "String")) return this.cx((a.t === "String" && av !== null ? av : jstr(av, a.t)) + (b.t === "String" && bv !== null ? bv : jstr(bv, b.t)), "String");
      if (["==", "!=", "<", ">", "<=", ">="].includes(op)) { v = op === "==" ? av === bv : op === "!=" ? av !== bv : op === "<" ? av < bv : op === ">" ? av > bv : op === "<=" ? av <= bv : av >= bv; return this.cx(v, "boolean"); }
      [a, b, t] = this.promote(a, b, line, op); av = a.const; bv = b.const;
      if (["<<", ">>", ">>>"].includes(op)) {
        t = a.t === "long" ? "long" : "int";
        const mask = t === "long" ? 63n : 31, sh = Number(BigInt(bv) & BigInt(mask));
        if (t === "long") v = op === "<<" ? wrap64(av << BigInt(sh)) : op === ">>" ? av >> BigInt(sh) : wrap64(BigInt.asUintN(64, av) >> BigInt(sh));
        else v = op === "<<" ? wrap32(BigInt(av) << BigInt(sh)) : op === ">>" ? av >> sh : wrap32(av >>> sh);
        return this.cx(v, t);
      }
      if (["&", "|", "^"].includes(op)) { if (t === "long") v = op === "&" ? av & bv : op === "|" ? av | bv : av ^ bv; else v = op === "&" ? av & bv : op === "|" ? av | bv : av ^ bv; return this.cx(v, t); }
      if (t === "float") { v = op === "+" ? f32(av + bv) : op === "-" ? f32(av - bv) : op === "*" ? f32(av * bv) : op === "/" ? f32(av / bv) : f32(pyFloatMod(av, bv)); return this.cx(v, t); }
      if (t === "double") { v = op === "+" ? av + bv : op === "-" ? av - bv : op === "*" ? av * bv : op === "/" ? av / bv : pyFloatMod(av, bv); return this.cx(v, t); }
      if (op === "+") v = av + bv; else if (op === "-") v = av - bv; else if (op === "*") v = av * bv; else if (op === "/") v = idiv(av, bv); else v = imod(av, bv);
      return this.cx(t === "long" ? wrap64(v) : wrap32(v), t);
    } catch (e) { if (e instanceof PdeError && e.kind === "runtime") return null; throw e; }
  }
  binop(op, a, b, line) {
    if (op === "+" && [a.t, b.t].includes("String")) return this.concat(a, b, line);
    if (["==", "!="].includes(op)) return this.equality(op, a, b, line);
    if (["&", "|", "^"].includes(op) && a.t === "boolean" && b.t === "boolean") return new X("boolean", [...a.code, ...b.code, [{ "&": "IAND", "|": "IOR", "^": "IXOR" }[op]]]);
    if (["<<", ">>", ">>>"].includes(op)) {
      if (!INTS.includes(a.t) || !INTS.includes(b.t)) this.err(`${op} needs integers`, line); const t = a.t === "long" ? "long" : "int"; if (a.t !== t) a = this.conv(a, t, line);
      const f = this.fold(op, a, b, line); if (f) return f; const bc = [...b.code, ...(b.t === "long" ? [["L2I"]] : [])], name = { "<<": "SHL", ">>": "SHR", ">>>": "USHR" }[op]; return new X(t, [...a.code, ...bc, [`${t === "int" ? "I" : "L"}${name}`]]);
    }
    [a, b, ] = this.promote(a, b, line, op); const t = a.t, f = this.fold(op, a, b, line); if (f) return f; const p = { int: "I", long: "L", float: "F", double: "D" }[t];
    if (["<", ">", "<=", ">="].includes(op)) return new X("boolean", [...a.code, ...b.code, [`${p}CMP`, CMP[op]]]);
    if (["&", "|", "^"].includes(op)) { if (!["int", "long"].includes(t)) this.err(`${op} needs integers`, line); return new X(t, [...a.code, ...b.code, [`${p}${{ "&": "AND", "|": "OR", "^": "XOR" }[op]}`]]); }
    return new X(t, [...a.code, ...b.code, [`${p}${{ "+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "%": "REM" }[op]}`]]);
  }
  concat(a, b, line) { if (a.const !== NOCONST && b.const !== NOCONST) return this.fold("+", a, b, line); return new X("String", [...a.code, ...this.toStr(a.t, line), ...b.code, ...this.toStr(b.t, line), ["SCAT"]]); }
  equality(op, a, b, line) {
    if (NUMS.includes(a.t) && NUMS.includes(b.t)) {
      let t; [a, b, t] = this.promote(a, b, line, op); const f = this.fold(op, a, b, line); if (f) return f;
      return new X("boolean", [...a.code, ...b.code, [`${{ int: "I", long: "L", float: "F", double: "D" }[t]}CMP`, CMP[op]]]);
    }
    if ((a.t === "boolean") !== (b.t === "boolean")) this.err(`cannot compare ${tname(a.t)} with ${tname(b.t)}`, line);
    const f = this.fold(op, a, b, line); if (f) return f; if (a.t === "boolean") return new X("boolean", [...a.code, ...b.code, ["ICMP", CMP[op]]]); if (a.t === "String" && b.t === "String") return new X("boolean", [...a.code, ...b.code, ["SCMP", CMP[op]]]); return new X("boolean", [...a.code, ...b.code, ["ACMP", CMP[op]]]);
  }
  e_cond(n) {
    const [, line, c, an, bn] = n, cc = this.cond(c); let a = this.expr(an), b = this.expr(bn);
    if (NUMS.includes(a.t) && NUMS.includes(b.t) && a.t !== b.t) [a, b, ] = this.promote(a, b, line, "?:");
    else if (!teq(a.t, b.t)) { if (a.t === "null") a = new X(b.t, a.code); else if (b.t === "null") b = new X(a.t, b.code); else if (a.t === "String" || b.t === "String") { a = this.conv(a, "String", line); b = this.conv(b, "String", line); } }
    if (primSlot(a.t) !== primSlot(b.t)) this.unsup("a ?: whose two sides differ in type", line); const els = new Label(), end = new Label(); return new X(a.t, [...cc, ["JZ", els], ...a.code, ["JMP", end], ["L", els], ...b.code, ["L", end]]);
  }
  e_cast(n) { const [, line, t0, an] = n, t = norm(t0), x = this.expr(an); if (teq(t, x.t)) return x; if (NUMS.includes(t) && NUMS.includes(x.t)) return this.conv(x, t, line, true); if (t === "String" && x.t !== "String") { if (x.t === "null" || Array.isArray(x.t) && x.t[1] === "Object") return new X("String", x.code); this.err(`cannot cast ${tname(x.t)} to String`, line); } return this.conv(x, t, line, true); }

  lvalue(n) {
    const k = n[0], line = n[1];
    if (k === "name") { const name = n[2], loc = this.findLocal(name); if (loc) return [loc[1], "local", [], loc[0]]; if (this.cls && this.cls.fields.has(name)) { const [idx, t] = this.cls.fields.get(name); return [t, "field", [["LOAD", 1]], idx]; } if (this.gtypes.has(name)) { const [slot, t] = this.gtypes.get(name); return [t, "global", [], slot]; } if (Object.hasOwn(CONST, name) || ["width", "height", "frameCount"].includes(name)) this.err(`${name} cannot be changed`, line); this.err(`${name} is not defined`, line); }
    if (k === "index") { const arr = this.expr(n[2]); if (!(Array.isArray(arr.t) && arr.t[0] === "[]")) this.err(`${tname(arr.t)} is not an array`, line); let ix = this.expr(n[3]); if (!INTS.includes(ix.t) || ix.t === "long") { if (INTS.includes(ix.t)) ix = this.conv(ix, "int", line); else this.err("an array index must be an int", line); } return [arr.t[1], "index", [...arr.code, ...ix.code], kindOf(arr.t[1])]; }
    if (k === "field") { const obj = this.expr(n[2]), name = n[3], ot = obj.t; if (Array.isArray(ot) && ot[0] === "C" && this.classes.has(ot[1])) { const ci = this.classes.get(ot[1]); if (!ci.fields.has(name)) this.err(`${ot[1]} has no field ${name}`, line); const [idx, t] = ci.fields.get(name); return [t, "field", [...obj.code, ["NULLCHK"]], idx]; } if (Array.isArray(ot) && ot[0] === "C" && ot[1] === "PVector") this.unsup("PVector on the VM", line); this.err(`cannot assign to ${name} of a ${tname(ot)}`, line); }
    this.err("the left of an assignment must be a variable", line);
  }
  rw(kind, arg) { return kind === "local" ? [[[["LOAD", arg]], "DUP", [["STORE", arg]]]] [0] : kind === "global" ? [[[["GLOAD", arg]], "DUP", [["GSTORE", arg]]]] [0] : kind === "field" ? [[[["DUP"], ["GETF", arg]], "DUP_X1", [["PUTF", arg]]]] [0] : [[[["DUP2"], ["ALOAD", arg]], "DUP_X2", [["ASTORE", arg]]]] [0]; }
  e_assign(n) { const [, line, op, target, value] = n, [t, kind, pre, arg] = this.lvalue(target), [read, dup, store] = this.rw(kind, arg); if (op === "=") { const x = this.storeConv(this.valueFor(value, t, line), t, line); return new X(t, [...pre, ...x.code, [dup], ...store]); } const r = this.storeConv(this.binop(op.slice(0, -1), new X(t, read), this.expr(value), line), t, line); return new X(t, [...pre, ...r.code, [dup], ...store]); }
  incdec(n, prefix) {
    const [, line, op, target] = n, [t, kind, pre, arg] = this.lvalue(target); if (!NUMS.includes(t)) this.err(`${op} needs a number`, line); const d = op === "++" ? 1 : -1;
    if (kind === "local" && t === "int") return new X(t, prefix ? [["IINC", arg, d], ["LOAD", arg]] : [["LOAD", arg], ["IINC", arg, d]]);
    const [read, dup, store] = this.rw(kind, arg); let step;
    if (t === "float") step = [["FCONST", d], ["FADD"]]; else if (t === "double") step = [...this.pushConst(d, "double"), ["DADD"]]; else if (t === "long") step = [...this.pushConst(BigInt(d), "long"), ["LADD"]]; else step = [["ICONST", d], ["IADD"], ...({ int: [], char: [["I2C"]], short: [["I2S"]], byte: [["I2B"]] }[t])];
    return new X(t, prefix ? [...pre, ...read, ...step, [dup], ...store] : [...pre, ...read, [dup], ...step, ...store]);
  }
  e_pre(n) { return this.incdec(n, true); } e_post(n) { return this.incdec(n, false); }
  e_index(n) { const [, line, an, ixn] = n, arr = this.expr(an); if (!(Array.isArray(arr.t) && arr.t[0] === "[]")) this.err(`${tname(arr.t)} is not an array`, line); let ix = this.expr(ixn); if (!INTS.includes(ix.t) || ix.t === "long") { if (INTS.includes(ix.t)) ix = this.conv(ix, "int", line); else this.err("an array index must be an int", line); } return new X(arr.t[1], [...arr.code, ...ix.code, ["ALOAD", kindOf(arr.t[1])]]); }
  e_newarr(n) {
    const [, line, base0, dims, extra, init] = n, base = norm(base0); this.checkType(base, line); let t = base; for (let i = 0; i < dims.length + extra; i++) t = ["[]", t]; if (init !== null) return this.arrayInit(init, t);
    const code = []; for (const d of dims) { const x = this.expr(d); if (!INTS.includes(x.t)) this.err("an array size must be an int", line); if (x.t === "long") this.unsup("a long array size", line); code.push(...x.code); }
    let et = base; for (let i = 0; i < extra; i++) et = ["[]", et]; if (dims.length === 1) return new X(t, [...code, ["NEWARR", kindOf(et)]]); if (dims.length > 8) this.unsup("an array of more than 8 dimensions", line); return new X(t, [...code, ["NEWMULTI", kindOf(et), dims.length]]);
  }
  arrayInit(node, t) { const [, line, items] = node, et = t[1], k = kindOf(et), code = [["ICONST", items.length], ["NEWARR", k]]; items.forEach((it, i) => { let x; if (it[0] === "arrinit") { if (!(Array.isArray(et) && et[0] === "[]")) this.err("too many { } for this array type", line); x = this.arrayInit(it, et); } else x = this.storeConv(this.expr(it), et, line); code.push(["DUP"], ["ICONST", i], ...x.code, ["ASTORE", k]); }); return new X(t, code); }
  e_field(n) {
    const [, line, on, name] = n, obj = this.expr(on), ot = obj.t;
    if (Array.isArray(ot) && ot[0] === "[]" && name === "length") return new X("int", [...obj.code, ["ALEN"]]);
    if (Array.isArray(ot) && ot[0] === "static") return this.staticField(ot[1], name, line);
    if (Array.isArray(ot) && ot[0] === "C" && this.classes.has(ot[1])) { const ci = this.classes.get(ot[1]); if (!ci.fields.has(name)) this.err(`${ot[1]} has no field ${name}`, line); const [idx, t] = ci.fields.get(name); return new X(t, [...obj.code, ["NULLCHK"], ["GETF", idx]]); }
    if (Array.isArray(ot) && ot[0] === "C" && ot[1] === "PVector") this.unsup("PVector on the VM", line); this.err(`${tname(ot)} has no field ${name}`, line);
  }
  staticField(cls, name, line) {
    const vals = { "Math.PI": ["double", Math.PI], "Math.E": ["double", Math.E], "Integer.MAX_VALUE": ["int", 2147483647], "Integer.MIN_VALUE": ["int", -2147483648],
      "Float.MAX_VALUE": ["float", 3.4028234663852886e38], "Float.MIN_VALUE": ["float", 1.401298464324817e-45], "Long.MAX_VALUE": ["long", 9223372036854775807n],
      "Double.MAX_VALUE": ["double", Number.MAX_VALUE], "Float.POSITIVE_INFINITY": ["float", Infinity], "Float.NEGATIVE_INFINITY": ["float", -Infinity],
      "PApplet.PI": ["float", f32(Math.PI)], "PApplet.TWO_PI": ["float", f32(2 * Math.PI)] };
    const k = `${cls}.${name}`; if (Object.hasOwn(vals, k)) return this.cx(vals[k][1], vals[k][0]); this.err(`${k} is not available here`, line);
  }
  e_call(n) {
    const [, line, name, argn] = n, args = argn.map((a) => this.expr(a));
    if (this.cls && this.cls.methods.has(name)) { const f = this.pick(this.cls.methods.get(name), args); if (f) return this.call(f, args, line, [["LOAD", 1]]); }
    if (this.funcs.has(name)) { const f = this.pick(this.funcs.get(name), args); if (f) return this.call(f, args, line); }
    return builtinCall(this, name, args, line);
  }
  pick(sigs, args) {
    let best = null, bestScore = null;
    for (const sig of sigs) { if (sig.params.length !== args.length) continue; let score = 0, ok = true; for (let i = 0; i < args.length; i++) { const p = sig.params[i], a = args[i]; if (teq(p, a.t)) continue; if (NUMS.includes(p) && NUMS.includes(a.t)) score += RANK[a.t] <= RANK[p] ? 1 : 10; else if (a.t === "null" && isRef(p)) score++; else if (p === "String" || isRef(p) && isRef(a.t)) score += 2; else { ok = false; break; } } if (ok && (bestScore === null || score < bestScore)) { best = sig; bestScore = score; } }
    return best;
  }
  call(f, args, line, thisCode = null) { const code = []; args.forEach((a, i) => code.push(...this.storeConv(a, f.params[i], line).code)); if (thisCode) code.push(...thisCode, ["CALLM", f]); else code.push(["CALL", f]); return new X(f.ret === "void" ? "void" : f.ret, code); }
  e_new(n) {
    const [, line, t0, argn] = n, t = norm(t0), name = t[1], args = argn.map((a) => this.expr(a));
    if (this.classes.has(name)) { const ci = this.classes.get(name), ctor = ci.ctors.length ? this.pick(ci.ctors, args) : null; if (ci.ctors.length && !ctor) this.err(`no constructor of ${name} takes these arguments`, line); if (!ci.ctors.length && args.length) this.err(`${name} has no constructor with arguments`, line); const code = []; if (ctor) args.forEach((a, i) => code.push(...this.storeConv(a, ctor.params[i], line).code)); return new X(["C", name, []], [...code, ["CALL", ci.news.get(ctor)]]); }
    return newBuiltin(this, t, args, line);
  }
  e_mcall(n) { return methodCall(this, n); }
  assemble() { return assemble(this); }
}

function numConv(src, to) {
  if (to === "double") return src === "float" ? ["F2D"] : src === "long" ? ["L2D"] : ["I2D"];
  if (to === "float") return src === "double" ? ["D2F"] : src === "long" ? ["L2F"] : ["I2F"];
  if (FLOATS.includes(src)) { const p = src === "float" ? "F" : "D"; if (to === "long") return [`${p}2L`]; if (to === "int") return [`${p}2I`]; return [`${p}2I`, { short: "I2S", byte: "I2B", char: "I2C" }[to]]; }
  if (to === "long") return src === "long" ? [] : ["I2L"]; if (to === "int") return src === "long" ? ["L2I"] : [];
  const pre = src === "long" ? ["L2I"] : []; if (to === "short") return [...pre, ...(src === "byte" ? [] : ["I2S"])]; if (to === "byte") return [...pre, "I2B"]; if (to === "char") return [...pre, "I2C"]; throw new Error(`bad conversion ${src} ${to}`);
}

// ----------------------------------------------------------------------------- VM built-ins
function needNums(c, args, line, name) { for (const a of args) if (!NUMS.includes(a.t)) c.err(`${name}() needs numbers, got ${tname(a.t)}`, line); }
function dbl(c, x) { return x.t === "double" ? [...x.code] : [...x.code, [x.t === "float" ? "F2D" : x.t === "long" ? "L2D" : "I2D"]]; }
function flt(c, x, line) { return (x.t === "float" ? x : c.conv(x, "float", line, true)).code; }
function nat(c, name, code, argc, t = "void", flags = 0) { return new X(t, [...code, ["NATIVE", NATIVE[name], argc, flags]]); }
function ignore(c, args, line, ret = "void") { const code = []; for (const a of args) code.push(...a.code, ...(a.t === "void" ? [] : [["POP"]])); if (ret !== "void") c.unsup("an ignored call with a value", line); return new X("void", code); }
function api(native, arities, ret = "void", num = true) { return (c, name, args, line) => { if (!arities.includes(args.length)) c.err(`${name}() does not take ${args.length} argument(s)`, line); if (num) needNums(c, args, line, name); const code = []; for (const a of args) code.push(...dbl(c, a)); return nat(c, native, code, args.length, ret); }; }
function colour(native, ret = "void") { return (c, name, args, line) => { if (args.length < 1 || args.length > 4) c.err(`${name}() takes 1 to 4 numbers`, line); needNums(c, args, line, name); let mask = 0; const code = []; args.forEach((a, i) => { if (["int", "short", "byte", "char"].includes(a.t)) mask |= 1 << i; code.push(...dbl(c, a)); }); return nat(c, native, code, args.length, ret, mask); }; }
function hSize(c, name, args, line) { if (args.length < 2) c.err("size() takes a width and a height", line); needNums(c, args.slice(0, 2), line, "size"); let flags = 0; if (args.length >= 3 && args[2].t === "String") { if (args[2].const === NOCONST) c.unsup("size() with a renderer that is not a constant", line); flags = args[2].const === "processing.opengl.PGraphics3D" ? 1 : 0; } return nat(c, "size", [...dbl(c, args[0]), ...dbl(c, args[1])], 2, "void", flags); }
function hGet(c, name, args, line) { if (args.length !== 2) c.unsup("get() of an image on the VM", line); return api("get", [2], "int")(c, name, args, line); }
function hSet(c, name, args, line) { if (args.length === 3 && Array.isArray(args[2].t)) c.unsup("set() of an image on the VM", line); return api("set", [3])(c, name, args, line); }
function hRotate(c, name, args, line) { return args.length === 4 ? api("rotate_axis", [4])(c, name, args, line) : api("rotate", [1])(c, name, args, line); }
function hClock(what) { return (c) => { c.usesClock = true; return nat(c, "clock", [], 0, "int", ["second", "minute", "hour", "day", "month", "year"].indexOf(what)); }; }
function hBackground(c, name, args, line) {
  const names = args.map((a, i) => a.t === "String" ? i : -1).filter((i) => i >= 0); if (!names.length) return colour("background")(c, name, args, line);
  const k = names[0], nums = args.slice(0, k), after = args.slice(k + 1); let mask = 0; const code = [];
  nums.forEach((a, i) => { if (["int", "short", "byte", "char"].includes(a.t)) mask |= 1 << i; code.push(...dbl(c, a)); }); code.push(...args[k].code); for (const a of after) code.push(...dbl(c, a)); if (after.length) mask |= 0x80; return nat(c, "background_texture", code, args.length, "void", mask);
}
function hTexture(native) { return (c, name, args, line) => { if (!args.length || args[0].t !== "String" || args.length > 2) c.err(`${name}() takes a texture name and an optional number`, line); needNums(c, args.slice(1), line, name); const code = [...args[0].code]; if (args.length === 2) code.push(...dbl(c, args[1])); return nat(c, native, code, args.length, "void", args.length === 2 ? 1 : 0); }; }
function hText(c, name, args, line) {
  if (![3, 4, 5].includes(args.length)) c.err("text() takes a value and a position (or a box)", line); const a = args[0]; for (const x of args.slice(1)) if (!NUMS.includes(x.t)) c.err("text()'s position must be numbers", line); let s;
  if (a.t === "String") s = [...a.code]; else if (a.t === "char") s = [...a.code, ["TOSTR", TS.char]]; else if (["int", "long", "short", "byte"].includes(a.t)) s = [...a.code, ["TOSTR", TS[a.t === "long" ? "long" : "int"]]]; else if (FLOATS.includes(a.t)) s = [...a.code, ...(a.t === "float" ? [["F2D"]] : []), ["TOSTR", TS.textfloat]]; else if (Array.isArray(a.t) && a.t[0] === "[]" && a.t[1] === "char") s = [...a.code, ["NATIVE", NATIVE.s_fromchars, 1, 0]]; else if (a.t === "boolean") s = [...a.code, ["TOSTR", TS.boolean]]; else c.unsup(`text() of a ${tname(a.t)}`, line);
  const rest = args.slice(1); if (rest.length === 3 && c.p3d) return nat(c, "text_z", [...s, ...dbl(c, rest[0]), ...dbl(c, rest[1]), ...dbl(c, rest[2])], 4); if ([2, 3].includes(rest.length)) return nat(c, "text", [...s, ...dbl(c, rest[0]), ...dbl(c, rest[1])], 3); const code = [...s]; for (const x of rest) code.push(...dbl(c, x)); return nat(c, "text_box", code, 5);
}
function hTextWidth(c, name, args, line) { if (args.length !== 1) c.err(`${name}() does not take ${args.length} argument(s)`, line); if (args[0].t !== "String") c.unsup("textWidth() of a non-String", line); return nat(c, "text_width", args[0].code, 1, "float"); }
function hCreateFont(c, name, args, line) { if (!args.length) c.err("createFont() needs a name and a size", line); if (args.length >= 2) { if (!NUMS.includes(args[1].t)) c.unsup("createFont() with a non-number size", line); return nat(c, "create_font", dbl(c, args[1]), 1, ["C", "PFont", []]); } return nat(c, "create_font", c.pushConst(12, "double"), 1, ["C", "PFont", []]); }
function hTextFont(c, name, args, line) { if (!args.length) c.err("textFont() needs a font", line); const code = [...args[0].code]; if (args.length >= 2) { if (!NUMS.includes(args[1].t)) c.unsup("textFont() with a non-number size", line); code.push(...dbl(c, args[1])); } return nat(c, "text_font", code, Math.min(2, args.length)); }
function hueSat(native) { return (c, name, args, line) => { if (args.length !== 1 || !NUMS.includes(args[0].t)) c.unsup(`${name}() of a non-number`, line); return nat(c, native, dbl(c, args[0]), 1, "float"); }; }
function hRandom(c, name, args, line) { if (![1, 2].includes(args.length)) c.err("random() takes 1 or 2 numbers", line); needNums(c, args, line, "random"); const code = []; for (const a of args) code.push(...flt(c, a, line), ["F2D"]); return nat(c, args.length === 1 ? "random" : "random2", code, args.length, "float"); }
function hNoise(c, name, args, line) { if (args.length < 1 || args.length > 3) c.err("noise() takes 1 to 3 numbers", line); needNums(c, args, line, "noise"); const code = []; for (const a of args) code.push(...dbl(c, a)); return nat(c, "noise", code, args.length, "float"); }
const safeMath = (fn, x) => { const v = fn(x); return Number.isNaN(v) ? NaN : v; };
function math1(native) { return (c, name, args, line) => { if (args.length !== 1) c.err("this takes one number", line); needNums(c, args, line, "math"); const a = args[0].t === "float" ? args[0] : c.conv(args[0], "float", line, true); if (a.const !== NOCONST) { const fn = { sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos, atan: Math.atan, sqrt: Math.sqrt, exp: Math.exp, log: Math.log, degrees: (v) => v * 57.2957763671875, radians: (v) => v * 0.01745329238474369 }[name]; return c.cx(f32(safeMath(fn, a.const)), "float"); } return nat(c, native, [...a.code, ["F2D"]], 1, "float"); }; }
function fcall(native, counts, ret = "float") { return (c, name, args, line) => { if (!counts.includes(args.length)) c.err(`${name}() does not take ${args.length} argument(s)`, line); needNums(c, args, line, name); const code = []; for (const a of args) code.push(...flt(c, a, line), ["F2D"]); return nat(c, native, code, args.length, ret); }; }
function hAbs(c, name, args, line) { if (args.length !== 1 || !NUMS.includes(args[0].t)) c.err("abs() takes one number", line); const a = args[0]; if (a.t === "long") return new X("int", [...a.code, ["NATIVE", NATIVE.m_absl, 1, 0], ["L2I"]]); if (INTS.includes(a.t)) return nat(c, "absi", a.code, 1, "int"); return nat(c, "absf", [...a.code, ...(a.t === "double" ? [["D2F"]] : []), ["F2D"]], 1, "float"); }
function hMinMax(which) { return (c, name, args, line) => { if (args.length === 1) { const a = args[0]; if (!(Array.isArray(a.t) && a.t[0] === "[]" && NUMS.includes(a.t[1]))) c.err(`${name}() of one thing needs a number array`, line); if (a.t[1] === "int") return nat(c, `${which}_arri`, a.code, 1, "int"); if (a.t[1] === "float") return nat(c, `${which}_arrf`, a.code, 1, "float"); c.unsup(`${name}() of a ${tname(a.t)}`, line); } if (![2, 3].includes(args.length)) c.err(`${name}() takes 2 or 3 numbers`, line); needNums(c, args, line, name); const t = args.some((a) => FLOATS.includes(a.t)) ? "float" : "int", code = []; for (const a0 of args) { const a = a0.t === t ? a0 : c.conv(a0, t, line); code.push(...a.code, ...(t === "float" ? [["F2D"]] : [])); } return nat(c, `${which}${t === "float" ? "f" : "i"}`, code, args.length, t); }; }
function hConstrain(c, name, args, line) { if (args.length !== 3) c.err("constrain() takes 3 numbers", line); needNums(c, args, line, "constrain"); const t = args.some((a) => FLOATS.includes(a.t)) ? "float" : "int", code = []; for (const a0 of args) { const a = a0.t === t ? a0 : c.conv(a0, t, line); code.push(...a.code, ...(t === "float" ? [["F2D"]] : [])); } return nat(c, t === "float" ? "constrainf" : "constraini", code, 3, t); }
function hFloor(native) { return (c, name, args, line) => { if (args.length !== 1 || !NUMS.includes(args[0].t)) c.err("this takes one number", line); return nat(c, native, [...flt(c, args[0], line), ["F2D"]], 1, "int"); }; }

function hInt(c, name, args, line) { if (args.length !== 1) c.err("int() takes one value", line); const a = args[0]; if (NUMS.includes(a.t)) return c.conv(a, "int", line, true); if (a.t === "boolean") return new X("int", a.code); if (a.t === "String") return nat(c, "parse_int", a.code, 1, "int"); if (Array.isArray(a.t) && a.t[0] === "[]" && NUMS.includes(a.t[1])) return nat(c, "a_toint", a.code, 1, ["[]", "int"], KIND[a.t[1]]); c.err(`int() of a ${tname(a.t)}`, line); }
function hFloat(c, name, args, line) { if (args.length !== 1) c.err("float() takes one value", line); const a = args[0]; if (NUMS.includes(a.t)) return c.conv(a, "float", line, true); if (a.t === "String") return nat(c, "parse_float", a.code, 1, "float"); if (a.t === "boolean") return new X("float", [...a.code, ["I2F"]]); c.err(`float() of a ${tname(a.t)}`, line); }
function hStr(c, name, args, line) { if (args.length !== 1) c.err("str() takes one value", line); return c.conv(args[0], "String", line); }
function hChar(c, name, args, line) { if (args.length !== 1 || !NUMS.includes(args[0].t)) c.err("char() takes a number", line); return c.conv(args[0], "char", line, true); }
function hByte(c, name, args, line) { if (args.length !== 1 || !NUMS.includes(args[0].t)) c.err("byte() takes a number", line); return c.conv(args[0], "byte", line, true); }
function hBoolean(c, name, args, line) { if (args.length !== 1) c.err("boolean() takes one value", line); const a = args[0]; if (NUMS.includes(a.t)) { const choices = { float: [["FCONST", 0], ["FCMP", 1]], double: [...c.pushConst(0, "double"), ["DCMP", 1]], long: [...c.pushConst(0n, "long"), ["LCMP", 1]] }; const z = choices[a.t] || [["ICONST", 0], ["ICMP", 1]]; return new X("boolean", [...a.code, ...z]); } if (a.t === "String") return nat(c, "parse_bool", a.code, 1, "boolean"); if (a.t === "boolean") return a; c.err(`boolean() of a ${tname(a.t)}`, line); }
function hNf(kind) { return (c, name, args, line) => { if (![1, 2, 3].includes(args.length)) c.err("nf() takes 1 to 3 arguments", line); const a = args[0]; if (!NUMS.includes(a.t)) c.err("nf() formats a number", line); if (a.t === "long") c.unsup("nf() of a long", line); for (const x of args.slice(1)) if (!NUMS.includes(x.t)) c.unsup("nf() with non-number digits", line); const code = []; for (const x of args) code.push(...dbl(c, x)); return nat(c, "nf", code, args.length, "String", kind | (INTS.includes(a.t) ? 4 : 0) | (a.t === "double" ? 8 : 0)); }; }
function hHex(c, name, args, line) { if (!args.length || !NUMS.includes(args[0].t)) c.err("hex() takes a number", line); if (!["int", "char", "short", "byte"].includes(args[0].t) || args.length > 2) c.unsup("hex() of this type", line); const width = args[0].t === "int" ? 8 : args[0].t === "char" ? 4 : 2, code = [...args[0].code]; if (args.length > 1) { if (!["int", "short", "byte", "char"].includes(args[1].t)) c.unsup("hex() digits", line); code.push(...args[1].code); } return nat(c, "hex", code, args.length, "String", width); }
function hUnhex(c, name, args, line) { if (args.length !== 1 || args[0].t !== "String") c.unsup("unhex() of a non-String", line); return nat(c, "unhex", args[0].code, 1, "int"); }
function arrType(c, a, line, name) { if (!(Array.isArray(a.t) && a.t[0] === "[]")) c.err(`${name}() needs an array`, line); return a.t; }
function hAppend(c, name, args, line) { if (args.length !== 2) c.err("append() takes an array and a value", line); const t = arrType(c, args[0], line, "append"), v = c.storeConv(args[1], t[1], line); return nat(c, "a_append", [...args[0].code, ...v.code], 2, t, kindOf(t[1])); }
function arrSimple(native, counts) { return (c, name, args, line) => { if (!counts.includes(args.length)) c.unsup(`${name}() with ${args.length} arguments`, line); const t = arrType(c, args[0], line, name), code = [...args[0].code]; for (const a of args.slice(1)) { if (!["int", "short", "byte", "char"].includes(a.t)) c.unsup(`${name}() with a ${tname(a.t)}`, line); code.push(...a.code); } return nat(c, native, code, args.length, t, kindOf(t[1])); }; }
function hConcat(c, name, args, line) { const t = arrType(c, args[0], line, "concat"); if (args.length !== 2 || !teq(args[1].t, t)) c.unsup("concat() of different arrays", line); return nat(c, "a_concat", [...args[0].code, ...args[1].code], 2, t, kindOf(t[1])); }
function hSort(c, name, args, line) { const t = arrType(c, args[0], line, "sort"); if (args.length !== 1 || ![...NUMS, "String"].includes(t[1])) c.unsup("sort() of this array", line); return nat(c, "a_sort", args[0].code, 1, t, kindOf(t[1])); }
function hArrayCopy(c, name, args, line) { if (![2, 3, 5].includes(args.length)) c.err("arrayCopy() takes 2, 3 or 5 arguments", line); const code = []; args.forEach((a, i) => { if ((args.length !== 5 ? [0, 1] : [0, 2]).includes(i)) { if (!(Array.isArray(a.t) && a.t[0] === "[]")) c.unsup("arrayCopy() of a non-array", line); } else if (!["int", "short", "byte", "char"].includes(a.t)) c.unsup("arrayCopy() with a non-int count", line); code.push(...a.code); }); return nat(c, "a_copy", code, args.length); }
function hTrim(c, name, args, line) { if (args.length !== 1 || args[0].t !== "String") c.unsup("trim() of a non-String", line); return nat(c, "ptrim", args[0].code, 1, "String"); }
const unsupportedCall = (c, name, args, line) => c.unsup(`${name}() on the VM`, line);

const CALLS = new Map();
function calls(names, handler) { for (const name of names.split(" ")) CALLS.set(name, handler); }
CALLS.set("size", hSize); CALLS.set("smooth", api("smooth", [0, 1])); CALLS.set("noSmooth", api("no_smooth", [0]));
calls("frameRate delay pixelDensity cursor noCursor textMode", (c, n, a, l) => ignore(c, a, l));
CALLS.set("noLoop", api("no_loop", [0])); CALLS.set("loop", api("loop", [0])); CALLS.set("redraw", api("redraw", [0])); CALLS.set("exit", api("exit", [0])); CALLS.set("millis", api("millis", [0], "int"));
calls("println print printArray", (c, n, a, l) => ignore(c, a, l));
for (const n of ["second", "minute", "hour", "day", "month", "year"]) CALLS.set(n, hClock(n));
CALLS.set("hint", api("hint", [1])); CALLS.set("blendMode", api("blend_mode", [1])); CALLS.set("background", hBackground);
CALLS.set("texture", hTexture("texture")); CALLS.set("strokeTexture", hTexture("stroke_texture")); CALLS.set("noTexture", api("no_texture", [0])); CALLS.set("noStrokeTexture", api("no_stroke_texture", [0])); CALLS.set("textureSeed", api("texture_seed", [1]));
CALLS.set("fill", colour("fill")); CALLS.set("stroke", colour("stroke")); CALLS.set("color", colour("color", "int")); CALLS.set("noFill", api("no_fill", [0])); CALLS.set("noStroke", api("no_stroke", [0])); CALLS.set("clear", api("clear", [0]));
for (const [n, native, arities, ret = "void"] of [
  ["strokeWeight", "stroke_weight", [1]], ["strokeCap", "stroke_cap", [1]], ["strokeJoin", "stroke_join", [1]], ["colorMode", "color_mode", [1, 2, 4, 5]],
  ["red", "red", [1], "float"], ["green", "green", [1], "float"], ["blue", "blue", [1], "float"], ["alpha", "alpha", [1], "float"], ["brightness", "brightness", [1], "float"], ["lerpColor", "lerp_color", [3], "int"],
  ["point", "point", [2, 3]], ["line", "line", [4, 6]], ["rect", "rect", [4, 5, 8]], ["square", "square", [3]], ["ellipse", "ellipse", [4]], ["circle", "circle", [3]], ["arc", "arc", [6, 7]],
  ["triangle", "triangle", [6]], ["quad", "quad", [8]], ["bezier", "bezier", [8]], ["curve", "curve", [8]], ["beginShape", "begin_shape", [0, 1]], ["endShape", "end_shape", [0, 1]], ["vertex", "vertex", [2, 3, 4]],
  ["bezierVertex", "bezier_vertex", [6, 9]], ["quadraticVertex", "quadratic_vertex", [4, 6]], ["curveVertex", "curve_vertex", [2, 3]], ["beginContour", "begin_contour", [0]], ["endContour", "end_contour", [0]],
  ["rectMode", "rect_mode", [1]], ["ellipseMode", "ellipse_mode", [1]], ["curveTightness", "curve_tightness", [1]], ["bezierPoint", "bezier_point", [5], "float"], ["bezierTangent", "bezier_tangent", [5], "float"], ["curvePoint", "curve_point", [5], "float"],
  ["pushMatrix", "push_matrix", [0]], ["popMatrix", "pop_matrix", [0]], ["push", "push", [0]], ["pop", "pop", [0]], ["pushStyle", "push_style", [0]], ["popStyle", "pop_style", [0]], ["translate", "translate", [2, 3]],
  ["rotateZ", "rotate", [1]], ["scale", "scale", [1, 2, 3]], ["shearX", "shear_x", [1]], ["shearY", "shear_y", [1]], ["resetMatrix", "reset_matrix", [0]], ["screenX", "screen_x", [2, 3], "float"], ["screenY", "screen_y", [2, 3], "float"],
  ["textSize", "text_size", [1]], ["textAlign", "text_align", [1, 2]], ["textLeading", "text_leading", [1]], ["textAscent", "text_ascent", [0], "float"], ["textDescent", "text_descent", [0], "float"],
  ["loadPixels", "load_pixels", [0]], ["updatePixels", "update_pixels", [0, 4]], ["randomSeed", "random_seed", [1]], ["randomGaussian", "random_gaussian", [0], "float"], ["noiseSeed", "noise_seed", [1]], ["noiseDetail", "noise_detail", [1, 2]],
]) CALLS.set(n, api(native, arities, ret));
CALLS.set("hue", hueSat("hue")); CALLS.set("saturation", hueSat("saturation")); calls("tint noTint bezierDetail curveDetail", (c, n, a, l) => ignore(c, a, l)); CALLS.set("rotate", hRotate);
CALLS.set("text", hText); CALLS.set("textWidth", hTextWidth); CALLS.set("createFont", hCreateFont); CALLS.set("loadFont", hCreateFont); CALLS.set("textFont", hTextFont); CALLS.set("get", hGet); CALLS.set("set", hSet);
CALLS.set("random", hRandom); CALLS.set("noise", hNoise);
for (const n of ["sin", "cos", "tan", "asin", "acos", "atan", "sqrt", "exp", "log", "degrees", "radians"]) CALLS.set(n, math1(n));
CALLS.set("atan2", fcall("atan2", [2])); CALLS.set("pow", fcall("pow", [2])); CALLS.set("abs", hAbs); CALLS.set("min", hMinMax("min")); CALLS.set("max", hMinMax("max")); CALLS.set("constrain", hConstrain);
CALLS.set("map", fcall("map", [5])); CALLS.set("lerp", fcall("lerp", [3])); CALLS.set("norm", fcall("norm", [3])); CALLS.set("dist", fcall("dist", [4, 6])); CALLS.set("mag", fcall("mag", [2, 3])); CALLS.set("sq", fcall("sq", [1]));
CALLS.set("floor", hFloor("floor")); CALLS.set("ceil", hFloor("ceil")); CALLS.set("round", hFloor("round")); CALLS.set("int", hInt); CALLS.set("float", hFloat); CALLS.set("str", hStr); CALLS.set("char", hChar); CALLS.set("byte", hByte); CALLS.set("boolean", hBoolean);
CALLS.set("nf", hNf(0)); CALLS.set("nfs", hNf(1)); CALLS.set("nfp", hNf(2)); CALLS.set("nfc", hNf(3)); CALLS.set("hex", hHex); CALLS.set("unhex", hUnhex);
CALLS.set("append", hAppend); CALLS.set("shorten", arrSimple("a_shorten", [1])); CALLS.set("expand", arrSimple("a_expand", [1, 2])); CALLS.set("subset", arrSimple("a_subset", [2, 3])); CALLS.set("concat", hConcat); CALLS.set("reverse", arrSimple("a_reverse", [1])); CALLS.set("sort", hSort); CALLS.set("arrayCopy", hArrayCopy); CALLS.set("arraycopy", hArrayCopy); CALLS.set("trim", hTrim); calls("join split splitTokens", unsupportedCall);
for (const [n, native, arities, ret = "void"] of [
  ["rotateX", "rotate_x", [1]], ["rotateY", "rotate_y", [1]], ["box", "box", [1, 3]], ["sphere", "sphere", [1]], ["sphereDetail", "sphere_detail", [1, 2]], ["camera", "camera", [0, 9]],
  ["perspective", "perspective", [0, 4]], ["ortho", "ortho", [0, 4, 6]], ["frustum", "frustum", [6]], ["lights", "lights", [0]], ["noLights", "no_lights", [0]],
  ["ambientLight", "ambient_light", [3, 6]], ["directionalLight", "directional_light", [6]], ["pointLight", "point_light", [6]], ["spotLight", "spot_light", [11]],
  ["lightFalloff", "light_falloff", [3]], ["lightSpecular", "light_specular", [3]], ["shininess", "shininess", [1]], ["normal", "normal", [3]], ["screenZ", "screen_z", [2, 3], "float"],
  ["applyMatrix", "apply_matrix", [6, 16]], ["filter", "filter", [1, 2]],
]) CALLS.set(n, api(native, arities, ret));
CALLS.set("ambient", colour("ambient")); CALLS.set("specular", colour("specular")); CALLS.set("emissive", colour("emissive"));

function builtinCall(c, name, args, line) { const h = CALLS.get(name); if (!h) c.err(`${name}() is not defined`, line); return h(c, name, args, line); }

function newBuiltin(c, t, args, line) {
  const name = t[1];
  if (name === "String") { if (args.length === 1 && teq(args[0].t, ["[]", "char"])) return nat(c, "s_fromchars", args[0].code, 1, "String"); if (!args.length) return c.cx("", "String"); return c.conv(args[0], "String", line); }
  if (["PVector", "ArrayList", "List", "IntList", "FloatList", "StringList", "HashMap", "Map"].includes(name)) c.unsup(`new ${name}() on the VM`, line);
  c.err(`new ${name}() is not available here`, line);
}
function stringMethod(c, obj, name, args, line) {
  const n = args.length, ts = args.map((a) => a.t), recv = [...obj.code, ["NULLCHK"]];
  const ints = (i) => { if (!["int", "short", "byte", "char"].includes(ts[i])) c.unsup(`String.${name}() with a ${tname(ts[i])}`, line); return args[i].code; };
  if (name === "length" && n === 0) return nat(c, "s_length", recv, 1, "int");
  if (name === "charAt" && n === 1) return nat(c, "s_charat", [...recv, ...ints(0)], 2, "char");
  if (name === "substring" && [1, 2].includes(n)) return nat(c, "s_substring", [...recv, ...ints(0), ...(n === 2 ? ints(1) : [])], 1 + n, "String");
  if (["indexOf", "lastIndexOf"].includes(name) && [1, 2].includes(n)) { const isChar = INTS.includes(ts[0]); if (!isChar && ts[0] !== "String") c.unsup(`String.${name}() of a ${tname(ts[0])}`, line); if (ts[0] === "long") c.unsup(`String.${name}() of a long`, line); return nat(c, name === "indexOf" ? "s_indexof" : "s_lastindexof", [...recv, ...args[0].code, ...(n === 2 ? ints(1) : [])], 1 + n, "int", isChar ? 1 : 0); }
  if (["toUpperCase", "toLowerCase", "trim", "isEmpty", "hashCode"].includes(name) && n === 0) { const nm = { toUpperCase: "s_upper", toLowerCase: "s_lower", trim: "s_trim", isEmpty: "s_isempty", hashCode: "s_hashcode" }[name]; return nat(c, nm, recv, 1, nm === "s_isempty" ? "boolean" : nm === "s_hashcode" ? "int" : "String"); }
  if (name === "equals" && n === 1) return ts[0] !== "String" ? c.cx(false, "boolean") : nat(c, "s_equals", [...recv, ...args[0].code], 2, "boolean");
  if (name === "equalsIgnoreCase" && n === 1) { if (ts[0] !== "String") c.unsup("equalsIgnoreCase() of a non-String", line); return nat(c, "s_equalsic", [...args[0].code, ...recv], 2, "boolean"); }
  if (["startsWith", "endsWith", "contains"].includes(name) && n === 1) { if (ts[0] !== "String") c.unsup(`String.${name}() of a non-String`, line); return nat(c, { startsWith: "s_startswith", endsWith: "s_endswith", contains: "s_contains" }[name], [...recv, ...args[0].code, ["NULLCHK"]], 2, "boolean"); }
  if (name === "compareTo" && n === 1) { if (ts[0] !== "String") c.unsup("compareTo() of a non-String", line); return nat(c, "s_compareto", [...recv, ...args[0].code, ["NULLCHK"]], 2, "int"); }
  if (name === "concat" && n === 1) return c.binop("+", new X("String", recv), args[0], line);
  if (name === "replace" && n === 2) { const isChar = ts[0] === "char"; if (isChar && ts[1] !== "char" || !isChar && (ts[0] !== "String" || ts[1] !== "String")) c.unsup("String.replace() of these types", line); return nat(c, "s_replace", [...recv, ...args[0].code, ...(isChar ? [] : [["NULLCHK"]]), ...args[1].code, ...(isChar ? [] : [["NULLCHK"]])], 3, "String", isChar ? 1 : 0); }
  if (name === "toCharArray" && n === 0) return nat(c, "s_tochars", recv, 1, ["[]", "char"]);
  if (name === "repeat" && n === 1) return nat(c, "s_repeat", [...recv, ...ints(0)], 2, "String"); if (name === "split") c.unsup("String.split() on the VM", line);
  c.err(`String has no method ${name}() here`, line);
}
const MATH_ONE = { sin: "m_sin", cos: "m_cos", tan: "m_tan", asin: "m_asin", acos: "m_acos", atan: "m_atan", sqrt: "m_sqrt", exp: "m_exp", log: "m_log", floor: "m_floor", ceil: "m_ceil", toRadians: "m_toradians", toDegrees: "m_todegrees", cbrt: "m_cbrt", log10: "m_log10", signum: "m_signum", rint: "m_rint" };
function mathStatic(c, name, args, line) {
  const n = args.length, ts = args.map((a) => a.t);
  if (Object.hasOwn(MATH_ONE, name) && n === 1) { needNums(c, args, line, `Math.${name}`); return nat(c, MATH_ONE[name], dbl(c, args[0]), 1, "double"); }
  if (name === "abs" && n === 1) { const t = ["int", "long", "float", "double"].includes(ts[0]) ? ts[0] : "int", a = args[0]; return t === "int" ? nat(c, "absi", a.code, 1, "int") : t === "long" ? nat(c, "m_absl", a.code, 1, "long") : t === "float" ? nat(c, "absf", [...a.code, ["F2D"]], 1, "float") : nat(c, "m_absd", a.code, 1, "double"); }
  if (["min", "max"].includes(name) && n === 2) { needNums(c, args, line, `Math.${name}`); const [a, b, t] = c.promote(args[0], args[1], line, name); if (t === "int") return nat(c, `${name}i`, [...a.code, ...b.code], 2, "int"); if (t === "float") return nat(c, `${name}f`, [...a.code, ["F2D"], ...b.code, ["F2D"]], 2, "float"); return nat(c, `m_${name}${t === "long" ? "l" : "d"}`, [...a.code, ...b.code], 2, t); }
  if (["pow", "atan2", "hypot"].includes(name) && n === 2) { needNums(c, args, line, `Math.${name}`); return nat(c, `m_${name}`, [...dbl(c, args[0]), ...dbl(c, args[1])], 2, "double"); }
  if (name === "round" && n === 1) { if (ts[0] === "double") return nat(c, "m_roundd", args[0].code, 1, "long"); if (ts[0] !== "float") c.unsup("Math.round() of an int", line); return nat(c, "round", [...args[0].code, ["F2D"]], 1, "int"); }
  if (name === "random" && n === 0) return nat(c, "m_random", [], 0, "double"); c.err(`Math.${name}() is not available here`, line);
}
function staticCall(c, cls, name, args, line) {
  if (cls === "Math") return mathStatic(c, name, args, line); if (cls === "String" && name === "valueOf" && args.length === 1) return c.conv(args[0], "String", line);
  if (["Integer", "Float", "Double", "Long"].includes(cls) && ["parseInt", "parseFloat", "valueOf", "parseDouble"].includes(name) && args.length === 1) return ["Integer", "Long"].includes(cls) ? hInt(c, name, args, line) : hFloat(c, name, args, line);
  if (cls === "Integer" && name === "toString" && args.length === 1) return c.conv(args[0], "String", line);
  if (cls === "Character" && ["isLetter", "isDigit", "isWhitespace", "isUpperCase", "isLowerCase"].includes(name) && args.length === 1) { if (!["int", "short", "byte", "char"].includes(args[0].t)) c.unsup(`Character.${name}() of a ${tname(args[0].t)}`, line); return nat(c, "char_test", args[0].code, 1, "boolean", ["isLetter", "isDigit", "isWhitespace", "isUpperCase", "isLowerCase"].indexOf(name)); }
  if (cls === "PApplet") return builtinCall(c, name, args, line); if (cls === "PVector") c.unsup("PVector on the VM", line); c.err(`${cls}.${name}() is not available here`, line);
}
function methodCall(c, n) {
  const [, line, on, name, argn] = n;
  if (on[0] === "field" && teq(on[2], ["name", on[2][1], "System"]) && ["out", "err"].includes(on[3])) return ignore(c, argn.map((a) => c.expr(a)), line);
  const obj = c.expr(on), args = argn.map((a) => c.expr(a)), ot = obj.t;
  if (Array.isArray(ot) && ot[0] === "static") return staticCall(c, ot[1], name, args, line); if (ot === "String") return stringMethod(c, obj, name, args, line);
  if (Array.isArray(ot) && ot[0] === "[]") { if (name === "clone" && !args.length) return nat(c, "a_clone", obj.code, 1, ot, kindOf(ot[1])); c.err(`arrays have no method ${name}() here (use .length)`, line); }
  if (Array.isArray(ot) && ot[0] === "C") { if (["PVector", "ArrayList", "List"].includes(ot[1])) c.unsup(`${ot[1]} on the VM`, line); if (c.classes.has(ot[1])) { const ci = c.classes.get(ot[1]); if (!ci.methods.has(name)) c.err(`${ot[1]} has no method ${name}()`, line); const f = c.pick(ci.methods.get(name), args); if (!f) c.err(`no ${ot[1]}.${name}() takes these arguments`, line); return c.call(f, args, line, obj.code); } if (ot[1] === "PFont") c.err(`PFont has no method ${name}() here`, line); }
  c.err(`${tname(ot)} has no method ${name}()`, line);
}

// ----------------------------------------------------------------------------- assembly
const FMT_SIZE = { i: 4, f: 4, H: 2, B: 1, b: 1 };
class Bytes {
  constructor() { this.a = []; }
  u8(v) { this.a.push(Number(v) & 255); }
  i8(v) { this.u8(v); }
  u16(v) { v = Number(v); this.a.push(v & 255, v >>> 8 & 255); }
  i16(v) { this.u16(v); }
  u32(v) { v = Number(v) >>> 0; this.a.push(v & 255, v >>> 8 & 255, v >>> 16 & 255, v >>> 24 & 255); }
  i32(v) { this.u32(v); }
  i64(v) { let n = BigInt.asUintN(64, BigInt(v)); for (let i = 0; i < 8; i++) { this.a.push(Number(n & 255n)); n >>= 8n; } }
  f32(v) { const b = new ArrayBuffer(4); new DataView(b).setFloat32(0, Number(v), true); this.raw(new Uint8Array(b)); }
  f64(v) { const b = new ArrayBuffer(8); new DataView(b).setFloat64(0, Number(v), true); this.raw(new Uint8Array(b)); }
  raw(v) { for (const x of v) this.a.push(x); }
  out() { return Uint8Array.from(this.a); }
}
function stackEffect(ins) {
  if (ins[0] === "CALL") return [ins[1].params.length, ins[1].ret === "void" ? 0 : 1];
  if (ins[0] === "CALLM") return [ins[1].params.length + 1, ins[1].ret === "void" ? 0 : 1];
  if (ins[0] === "NATIVE") return [ins[2], NATIVE_ROWS[ins[1]][1] ? 1 : 0];
  if (ins[0] === "NEWMULTI") return [ins[2], 1]; const row = BC_OPS[OP[ins[0]]]; return [row[2], row[3]];
}
function encodeFunc(f) {
  let pos = 0;
  for (const ins of f.code) { if (ins[0] === "L") { ins[1].pos = pos; continue; } for (const ch of BC_OPS[OP[ins[0]]][1]) pos += FMT_SIZE[ch]; pos++; }
  if (pos > MAX_CODE) throw new Unsupported(`${f.name}() is too long for the VM`);
  const out = new Bytes();
  for (const ins of f.code) {
    if (ins[0] === "L") continue; const fmt = BC_OPS[OP[ins[0]]][1]; out.u8(OP[ins[0]]);
    [...fmt].forEach((ch, i) => { let v = ins[i + 1]; if (v instanceof Label) v = v.pos; else if (v instanceof Func) v = v.index; if (ch === "f") out.f32(v); else if (ch === "i") out.i32(v); else if (ch === "H") out.u16(v); else if (ch === "B") out.u8(v); else out.i8(v); });
  }
  const depthAt = new Map(), idxOfPos = new Map(), items = f.code.filter((x) => x[0] !== "L"), offs = []; let p = 0;
  items.forEach((ins, i) => { idxOfPos.set(p, i); offs.push(p); p += 1 + [...BC_OPS[OP[ins[0]]][1]].reduce((a, ch) => a + FMT_SIZE[ch], 0); });
  const work = [[0, 0]]; let best = 0;
  while (work.length) {
    let [i, d] = work.pop();
    while (i < items.length) {
      if (depthAt.has(offs[i])) { if (depthAt.get(offs[i]) !== d) throw new Error(`stack depth mismatch in ${f.name} at ${offs[i]}`); break; }
      depthAt.set(offs[i], d); const ins = items[i], [pops, pushes] = stackEffect(ins); if (d < pops) throw new Error(`stack underflow in ${f.name} at ${offs[i]} (${ins[0]})`); d = d - pops + pushes; best = Math.max(best, d);
      if (["RET", "RETV", "FAULT"].includes(ins[0])) break;
      if (["JMP", "JZ", "JNZ", "LOOP"].includes(ins[0])) { const tgt = idxOfPos.has(ins[1].pos) ? idxOfPos.get(ins[1].pos) : items.length; if (["JMP", "LOOP"].includes(ins[0])) { i = tgt; continue; } work.push([tgt, d]); }
      i++;
    }
  }
  return [out.out(), best];
}
function assemble(bc) {
  const blobs = bc.allFuncs.map((f) => { const [code, depth] = encodeFunc(f); if (f.nslots > 1020 || f.params.length > 250) throw new Unsupported(`${f.name}() has too many variables for the VM`); return [f, code, depth]; });
  const out = new Bytes(); out.raw([80, 68, 69, 66]); out.u8(BC_VERSION); out.u8(bc.usesClock ? 1 : 0); out.u16(0);
  for (const k of ["globals", "static", "setup", "draw"]) out.i16(bc.entry[k]);
  const gl = [...bc.gtypes.values()].sort((a, b) => a[0] - b[0]); out.u16(gl.length); for (const [, t] of gl) out.u8(primSlot(t).charCodeAt(0));
  out.u16(bc.classes.size); for (const ci of bc.classes.values()) { const fl = [...ci.fields.values()].sort((a, b) => a[0] - b[0]); out.u16(fl.length); for (const [, t] of fl) out.u8(primSlot(t).charCodeAt(0)); }
  out.u16(bc.longs.size); for (const [, v] of bc.longs.values()) out.i64(v); out.u16(bc.doubles.size); for (const [, v] of bc.doubles.values()) out.f64(v);
  out.u16(bc.strings.size); for (const [, v] of bc.strings.values()) { out.u16(v.length); for (let i = 0; i < v.length; i++) out.u16(v.charCodeAt(i)); }
  out.u16(bc.allFuncs.length); let off = 0;
  for (const [f, code, depth] of blobs) { out.u8(f.params.length); out.u8((f.hasThis ? 1 : 0) | (f.kind === "new" ? 2 : 0)); out.u16(f.nslots); out.u16(depth + 2); out.u8(f.ret === "void" ? 0 : 1); out.u32(off); out.u16(code.length); off += code.length; }
  out.u32(off); for (const [, code] of blobs) out.raw(code); return out.out();
}
function callsSizeP3d(node) {
  const stack = [node]; while (stack.length) { const n = stack.pop(); if (Array.isArray(n)) { if (n.length === 4 && n[0] === "call" && n[2] === "size" && Array.isArray(n[3]) && n[3].length === 3) { const a = n[3][2]; if (Array.isArray(a) && a[0] === "name" && a.at(-1) === "P3D") return true; } for (const x of n) if (Array.isArray(x)) stack.push(x); } } return false;
}

// mathlib.py's deliberately light linker. The build ships the prelude as top-level chunks with
// its dependency metadata, and this repeats split_plan() after every edit: a newly added helper
// call links exactly what Python would, while an own definition with the same name still wins.
function libraryDefs(toks) {
  const funcs = new Set(), classes = new Set(), globs = new Set(); let depth = 0, paren = 0;
  for (let i = 0; i < toks.length; i++) {
    const [k, t] = toks[i];
    if (k === "op") {
      if (t === "{") depth++; else if (t === "}") depth = Math.max(0, depth - 1);
      else if (t === "(") paren++; else if (t === ")") paren = Math.max(0, paren - 1);
      continue;
    }
    if (depth || k !== "id") continue;
    const prev = i ? toks[i - 1] : ["op", ";", 0], next = i + 1 < toks.length ? toks[i + 1] : ["op", ";", 0];
    if (prev[1] === "class") classes.add(t);
    else if (!paren && next[1] === "(" && (prev[0] === "id" || ["]", ">"].includes(prev[1])) && prev[1] !== "new") funcs.add(t);
    else if (!paren && ["=", ";", ","].includes(next[1]) && (prev[0] === "id" || ["]", ">", ","].includes(prev[1]))) globs.add(t);
  }
  return [funcs, classes, globs];
}

function selectLibrary(src, library) {
  const chunks = library.chunks || [];
  if (!chunks.length) return { pre: library.pre || [], post: library.post || [] };
  const toks = tokenize(src, 1, false), [funcs, classes, globs] = libraryDefs(toks);
  const byName = new Map(), libClasses = new Set(), constants = new Set();
  chunks.forEach((ch, i) => {
    for (const name of ch.names) { byName.set(name, i); if (ch.kind === "class") libClasses.add(name); if (ch.kind === "global" && !name.startsWith("_")) constants.add(name); }
  });
  const own = new Set([...funcs, ...classes, ...[...globs].filter((x) => constants.has(x))]);
  const used = new Set();
  toks.forEach(([k, t], i) => {
    if (k !== "id" || (i && toks[i - 1][1] === ".")) return;
    const next = i + 1 < toks.length ? toks[i + 1][1] : "";
    if (next === "(" || libClasses.has(t) || constants.has(t)) used.add(t);
  });
  const stack = [...used].filter((n) => byName.has(n) && !own.has(n)).map((n) => byName.get(n)), need = new Set();
  while (stack.length) {
    const i = stack.pop(); if (need.has(i)) continue; need.add(i);
    for (const name of chunks[i].refs) if (!own.has(name) && byName.has(name)) stack.push(byName.get(name));
  }
  const callbacks = library.callbacks || {};
  for (const i of [...need].sort((a, b) => a - b)) for (const name of chunks[i].names) {
    const cb = callbacks[name];
    if (cb && !funcs.has(cb[0])) throw new PdeError("compile", `${name}() needs your own function: ${cb[1]}`, 0);
  }
  const ordered = [...need].sort((a, b) => a - b);
  return { pre: ordered.filter((i) => chunks[i].kind === "global").map((i) => chunks[i]),
           post: ordered.filter((i) => chunks[i].kind !== "global").map((i) => chunks[i]) };
}

/** Compile one editable sketch. library is the build-time mathlib selection for its original id. */
export function compileBytecode(src, library = {}) {
  try {
    const sourceAst = parse(src), plan = selectLibrary(src, library), classes = library.classes || [], pre = [], post = [];
    for (const ch of plan.pre) pre.push(...parse(ch.text, classes, ch.line, false));
    for (const ch of plan.post) post.push(...parse(ch.text, classes, ch.line, false));
    const ast = [...pre, ...sourceAst, ...post], bc = new BCompiler(); bc.p3d = callsSizeP3d(ast); bc.compile(ast); return bc.assemble();
  } catch (e) {
    if (e instanceof PdeError || e instanceof Unsupported) throw e;
    if (e instanceof RangeError) throw new PdeError("depth", "the code is nested too deeply");
    throw e;
  }
}

export function compileResult(src, library = {}) {
  try { return { ok: true, bytes: compileBytecode(src, library) }; }
  catch (e) { if (e instanceof PdeError || e instanceof Unsupported) return { ok: false, kind: e.kind, line: e.line || 0, message: e.message }; throw e; }
}
