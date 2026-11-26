"""Tokens -> AST for the Processing (Java-mode) subset.

Nodes are tuples whose first item is the node kind and second the source line. Types are
  a primitive name ("int", "float", "double", "long", "boolean", "char", "byte", "short", "void"),
  "String", ("[]", T) for an array of T, or ("C", name, (type args)) for a class.
`color` is read as "int", as Processing's preprocessor does.

Recursion is bounded (MAX_NEST) so a hostile sketch cannot exhaust the Python stack here.
"""
from __future__ import annotations

from .errors import PdeError
from .lexer import tokenize

MAX_NEST = 40
PRIMS = ("int", "float", "double", "long", "boolean", "char", "byte", "short", "void", "color")
KEYWORDS = {"if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "return",
            "new", "class", "true", "false", "null", "this", "static", "final", "public", "private",
            "protected", "import", "extends", "implements", "interface", "try", "catch", "finally", "throw",
            "throws", "super", "instanceof", "abstract", "synchronized", "volatile", "transient", "native",
            "enum", "package", "goto", "const", "assert"}
MODIFIERS = {"static", "final", "public", "private", "protected", "abstract", "synchronized", "volatile",
             "transient", "native"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", ">>>="}
BINARY = {  # op -> precedence (higher binds tighter)
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8, ">>>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}


def prim(t) -> str:
    return "int" if t == "color" else t


class Parser:
    def __init__(self, src: str, classes=(), first_line: int = 1, bounded: bool = True) -> None:
        self.toks = tokenize(src, first_line, bounded)
        self.i = 0
        self.depth = 0
        self.classes: set[str] = set(classes)
        # a first pass for class names, so `Foo f = ...` and `(Foo) x` read as types
        for k in range(len(self.toks) - 1):
            if self.toks[k] == ("id", "class", self.toks[k][2]) and self.toks[k + 1][0] == "id":
                self.classes.add(self.toks[k + 1][1])

    # ------------------------------------------------------------------ tokens
    def peek(self, k: int = 0) -> tuple:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    @property
    def line(self) -> int:
        return self.peek()[2]

    def next(self) -> tuple:
        t = self.toks[self.i]
        if t[0] != "eof":
            self.i += 1
        return t

    def is_op(self, v: str, k: int = 0) -> bool:
        t = self.peek(k)
        return t[0] == "op" and t[1] == v

    def is_id(self, v: str | None = None, k: int = 0) -> bool:
        t = self.peek(k)
        return t[0] == "id" and (v is None or t[1] == v)

    def accept(self, v: str) -> bool:
        if self.is_op(v) or self.is_id(v):
            self.i += 1
            return True
        return False

    def expect(self, v: str) -> None:
        if not self.accept(v):
            t = self.peek()
            got = "the end" if t[0] == "eof" else repr(t[1])
            raise PdeError("syntax", f"expected {v!r}, found {got}", t[2])

    def ident(self) -> str:
        t = self.peek()
        if t[0] != "id" or t[1] in KEYWORDS:
            raise PdeError("syntax", f"expected a name, found {t[1]!r}", t[2])
        self.i += 1
        return t[1]

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_NEST:
            raise PdeError("depth", "the code is nested too deeply", self.line)

    def leave(self) -> None:
        self.depth -= 1

    # ------------------------------------------------------------------ types
    def try_type(self):
        """Parse a type at the cursor, or return None (cursor unchanged)."""
        start = self.i
        t = self.peek()
        if t[0] != "id":
            return None
        name = t[1]
        if name in PRIMS:
            self.i += 1
            ty = prim(name)
        elif name in KEYWORDS:
            return None
        else:
            self.i += 1
            args: tuple = ()
            if self.is_op("<"):
                got = self.type_args()
                if got is None:
                    self.i = start
                    return None
                args = got
            # qualified names like java.util.ArrayList -> the last part
            while self.is_op(".") and self.is_id(k=1) and name[0].islower():
                self.i += 1
                name = self.ident()
                if self.is_op("<"):
                    got = self.type_args()
                    if got is None:
                        self.i = start
                        return None
                    args = got
            ty = "String" if name == "String" else ("C", name, args)
        while self.is_op("[") and self.is_op("]", 1):
            self.i += 2
            ty = ("[]", ty)
        return ty

    def type_args(self):
        """`<T, U>` after a class name (diamond `<>` allowed); None if it is not a type list."""
        save = self.i
        self.i += 1
        args = []
        if self.is_op(">"):
            self.i += 1
            return ()
        while True:
            if self.is_op("?"):
                self.i += 1
                ty = ("C", "Object", ())
            else:
                ty = self.try_type()
            if ty is None:
                self.i = save
                return None
            args.append(ty)
            if self.accept(","):
                continue
            t = self.peek()
            if t[0] == "op" and t[1] in (">", ">>", ">>>"):
                # split a `>>` that closes two type lists
                if t[1] == ">":
                    self.i += 1
                else:
                    self.toks[self.i] = ("op", t[1][1:], t[2])
                return tuple(args)
            self.i = save
            return None

    def looks_like_decl(self) -> bool:
        """At a statement: is this `Type name ...` (a declaration)?"""
        save = self.i
        try:
            while self.is_id() and self.peek()[1] in MODIFIERS:
                self.i += 1
            ty = self.try_type()
            if ty is None:
                return False
            t = self.peek()
            return t[0] == "id" and t[1] not in KEYWORDS and (
                self.peek(1)[0] == "op" and self.peek(1)[1] in ("=", ";", ",", "[", ":", ")"))
        finally:
            self.i = save

    # ------------------------------------------------------------------ program
    def program(self) -> list:
        items = []
        while self.peek()[0] != "eof":
            if self.accept(";"):
                continue
            if self.is_id("import"):
                raise PdeError("compile", "import is not available here", self.line)
            items.append(self.member(top=True))
        return items

    def member(self, top: bool, cls: str | None = None):
        line = self.line
        while self.is_id() and self.peek()[1] in MODIFIERS:
            self.i += 1
        if self.is_id("class"):
            return self.class_decl()
        if self.is_id("interface") or self.is_id("enum"):
            raise PdeError("compile", f"{self.peek()[1]} is not available here", line)
        # constructor: Name ( ... ) {
        if cls is not None and self.is_id(cls) and self.is_op("(", 1):
            self.i += 1
            params = self.params()
            return ("ctor", line, cls, params, self.block())
        save = self.i
        ty = self.try_type()
        if ty is not None and self.is_id() and self.peek()[1] not in KEYWORDS:
            name = self.peek()[1]
            if self.is_op("(", 1):
                self.i += 1
                params = self.params()
                if self.is_id("throws"):
                    raise PdeError("compile", "exceptions are not available here", line)
                return ("func", line, ty, name, params, self.block())
            self.i = save
            decl = self.var_decl()
            self.expect(";")
            return ("global", line, decl)
        self.i = save
        if not top:
            raise PdeError("syntax", "expected a field or a method in the class", line)
        return ("stmt", line, self.statement())

    def class_decl(self):
        line = self.line
        self.expect("class")
        name = self.ident()
        if self.is_id("extends") or self.is_id("implements"):
            raise PdeError("compile", "extends/implements are not available here", line)
        self.expect("{")
        members = []
        while not self.accept("}"):
            if self.peek()[0] == "eof":
                raise PdeError("syntax", "unfinished class", line)
            if self.accept(";"):
                continue
            members.append(self.member(top=False, cls=name))
        return ("class", line, name, members)

    def params(self) -> list:
        self.expect("(")
        out = []
        if not self.accept(")"):
            while True:
                while self.is_id("final"):
                    self.i += 1
                ty = self.try_type()
                if ty is None:
                    raise PdeError("syntax", "expected a parameter type", self.line)
                if self.accept("..."):
                    ty = ("[]", ty)
                name = self.ident()
                while self.is_op("[") and self.is_op("]", 1):
                    self.i += 2
                    ty = ("[]", ty)
                out.append((ty, name))
                if self.accept(")"):
                    break
                self.expect(",")
        return out

    # ------------------------------------------------------------------ statements
    def block(self):
        line = self.line
        self.expect("{")
        self.enter()
        stmts = []
        while not self.accept("}"):
            if self.peek()[0] == "eof":
                raise PdeError("syntax", "a { is never closed", line)
            stmts.append(self.statement())
        self.leave()
        return ("block", line, stmts)

    def var_decl(self):
        line = self.line
        while self.is_id() and self.peek()[1] in MODIFIERS:
            self.i += 1
        ty = self.try_type()
        if ty is None:
            raise PdeError("syntax", "expected a type", line)
        out = []
        while True:
            name = self.ident()
            t = ty
            while self.is_op("[") and self.is_op("]", 1):
                self.i += 2
                t = ("[]", t)
            init = None
            if self.accept("="):
                init = self.array_init() if self.is_op("{") else self.expr()
            out.append((t, name, init))
            if not self.accept(","):
                break
        return ("vardecl", line, out)

    def array_init(self):
        line = self.line
        self.expect("{")
        self.enter()
        items = []
        while not self.accept("}"):
            items.append(self.array_init() if self.is_op("{") else self.expr())
            if not self.accept(","):
                self.expect("}")
                break
        self.leave()
        return ("arrinit", line, items)

    def statement(self):
        self.enter()
        try:
            return self._statement()
        finally:
            self.leave()

    def _statement(self):
        line = self.line
        t = self.peek()
        if self.is_op("{"):
            return self.block()
        if self.accept(";"):
            return ("empty", line)
        if t[0] == "id":
            w = t[1]
            if w == "if":
                self.i += 1
                self.expect("(")
                c = self.expr()
                self.expect(")")
                then = self.statement()
                other = self.statement() if self.accept("else") else None
                return ("if", line, c, then, other)
            if w == "while":
                self.i += 1
                self.expect("(")
                c = self.expr()
                self.expect(")")
                return ("while", line, c, self.statement())
            if w == "do":
                self.i += 1
                body = self.statement()
                self.expect("while")
                self.expect("(")
                c = self.expr()
                self.expect(")")
                self.expect(";")
                return ("do", line, body, c)
            if w == "for":
                return self.for_stmt()
            if w == "switch":
                return self.switch_stmt()
            if w in ("break", "continue"):
                self.i += 1
                label = self.ident() if self.is_id() and self.peek()[1] not in KEYWORDS else None
                self.expect(";")
                return (w, line, label)
            if w == "return":
                self.i += 1
                e = None if self.is_op(";") else self.expr()
                self.expect(";")
                return ("return", line, e)
            if w in ("try", "throw", "synchronized", "assert"):
                raise PdeError("compile", f"{w} is not available here", line)
            if w == "class":
                raise PdeError("compile", "a class inside a function is not available here", line)
            # label:  for (...)
            if w not in KEYWORDS and self.is_op(":", 1) and not self.looks_like_decl():
                self.i += 2
                inner = self.statement()
                return ("labeled", line, w, inner)
            if self.looks_like_decl():
                d = self.var_decl()
                self.expect(";")
                return d
        e = self.expr()
        self.expect(";")
        return ("expr", line, e)

    def for_stmt(self):
        line = self.line
        self.expect("for")
        self.expect("(")
        # for (Type name : iterable)
        save = self.i
        while self.is_id("final"):
            self.i += 1
        ty = self.try_type()
        if ty is not None and self.is_id() and self.is_op(":", 1):
            name = self.ident()
            self.expect(":")
            it = self.expr()
            self.expect(")")
            return ("foreach", line, ty, name, it, self.statement())
        self.i = save
        init: list = []
        if not self.is_op(";"):
            if self.looks_like_decl():
                init = [self.var_decl()]
            else:
                init = [("expr", line, self.expr())]
                while self.accept(","):
                    init.append(("expr", line, self.expr()))
        self.expect(";")
        cond = None if self.is_op(";") else self.expr()
        self.expect(";")
        update = []
        if not self.is_op(")"):
            update.append(self.expr())
            while self.accept(","):
                update.append(self.expr())
        self.expect(")")
        return ("for", line, init, cond, update, self.statement())

    def switch_stmt(self):
        line = self.line
        self.expect("switch")
        self.expect("(")
        e = self.expr()
        self.expect(")")
        self.expect("{")
        cases = []   # [(labels or None for default, [stmts])]
        while not self.accept("}"):
            if self.peek()[0] == "eof":
                raise PdeError("syntax", "unfinished switch", line)
            labels: list | None = []
            if self.accept("default"):
                labels = None
                self.expect(":")
            else:
                self.expect("case")
                labels.append(self.expr())
                while self.accept(","):
                    labels.append(self.expr())
                self.expect(":")
            body = []
            while not (self.is_id("case") or self.is_id("default") or self.is_op("}")):
                if self.peek()[0] == "eof":
                    raise PdeError("syntax", "unfinished switch", line)
                body.append(self.statement())
            cases.append((labels, body))
        return ("switch", line, e, cases)

    # ------------------------------------------------------------------ expressions
    def expr(self):
        self.enter()
        try:
            return self.assignment()
        finally:
            self.leave()

    def assignment(self):
        left = self.ternary()
        t = self.peek()
        if t[0] == "op" and t[1] in ASSIGN_OPS:
            if left[0] not in ("name", "field", "index"):
                raise PdeError("syntax", "the left of an assignment must be a variable", t[2])
            self.i += 1
            right = self.array_init() if self.is_op("{") else self.expr()
            return ("assign", t[2], t[1], left, right)
        return left

    def ternary(self):
        c = self.binary(1)
        if self.is_op("?"):
            line = self.line
            self.i += 1
            a = self.expr()
            self.expect(":")
            self.enter()
            try:
                b = self.ternary()
            finally:
                self.leave()
            return ("cond", line, c, a, b)
        return c

    def binary(self, min_prec: int):
        left = self.unary()
        while True:
            t = self.peek()
            if t[0] == "id" and t[1] == "instanceof":
                raise PdeError("compile", "instanceof is not available here", t[2])
            if t[0] != "op" or t[1] not in BINARY or BINARY[t[1]] < min_prec:
                return left
            op, prec = t[1], BINARY[t[1]]
            self.i += 1
            self.enter()
            try:
                right = self.binary(prec + 1)
            finally:
                self.leave()
            left = ("bin", t[2], op, left, right)

    def unary(self):
        t = self.peek()
        if t[0] == "op":
            if t[1] in ("-", "+", "!", "~"):
                self.i += 1
                self.enter()
                try:
                    # -2147483648 is legal: fold a minus straight onto an int literal
                    if t[1] == "-" and self.peek()[0] in ("int", "long", "float", "double") and not self.is_op(".", 1) \
                            and not self.is_op("[", 1):
                        k, v, ln = self.next()
                        return self.postfix(("lit", ln, "long" if k == "long" else "int" if k == "int" else k,
                                             _neg(k, v)))
                    return ("un", t[2], t[1], self.unary())
                finally:
                    self.leave()
            if t[1] in ("++", "--"):
                self.i += 1
                target = self.unary()
                if target[0] not in ("name", "field", "index"):
                    raise PdeError("syntax", f"{t[1]} needs a variable", t[2])
                return ("pre", t[2], t[1], target)
            if t[1] == "(":
                cast = self.try_cast()
                if cast is not None:
                    return cast
        return self.postfix(self.primary())

    def try_cast(self):
        save = self.i
        line = self.line
        self.i += 1
        t = self.peek()
        if t[0] == "id" and (t[1] in PRIMS or t[1] == "String" or t[1] in self.classes or t[1] == "PVector"):
            ty = self.try_type()
            if ty is not None and self.accept(")"):
                nxt = self.peek()
                is_prim = isinstance(ty, str) and ty in PRIMS or ty == "int"
                ok = nxt[0] in ("id", "int", "long", "float", "double", "char", "str", "color") or (
                    nxt[0] == "op" and nxt[1] in ("(", "!", "~") + (("-", "+", "++", "--") if is_prim else ()))
                if ok and not (nxt[0] == "id" and nxt[1] in ("instanceof",)):
                    self.enter()
                    try:
                        return ("cast", line, ty, self.unary())
                    finally:
                        self.leave()
        self.i = save
        return None

    def args(self) -> list:
        self.expect("(")
        out = []
        if not self.accept(")"):
            while True:
                out.append(self.expr())
                if self.accept(")"):
                    break
                self.expect(",")
        return out

    def primary(self):
        t = self.next()
        k, v, line = t
        if k in ("int", "long", "float", "double", "char", "str"):
            if k == "int" and v == 0x80000000:
                raise PdeError("syntax", "integer number too large", line)
            return ("lit", line, {"str": "String"}.get(k, k), v)
        if k == "color":
            return ("lit", line, "int", v - (1 << 32) if v >= 1 << 31 else v)
        if k == "op":
            if v == "(":
                e = self.expr()
                self.expect(")")
                return e
            raise PdeError("syntax", f"unexpected {v!r}", line)
        if k == "eof":
            raise PdeError("syntax", "the sketch ends in the middle of an expression", line)
        # identifiers and keywords
        if v == "true" or v == "false":
            return ("lit", line, "boolean", v == "true")
        if v == "null":
            return ("lit", line, "null", None)
        if v == "this":
            return ("this", line)
        if v == "new":
            return self.new_expr(line)
        if v in PRIMS and self.is_op("("):
            # Processing's int(x), float(x), boolean(x) ... conversions; color(...) is the function
            return ("call", line, v if v == "color" else prim(v), self.args())
        if v in PRIMS or v == "String":
            # float.class / int[].class never matter; String.valueOf(x) etc.
            if v == "String" and self.is_op("."):
                return ("name", line, v)
            raise PdeError("syntax", f"unexpected {v!r}", line)
        if v in KEYWORDS:
            raise PdeError("syntax", f"unexpected {v!r}", line)
        if self.is_op("("):
            return ("call", line, v, self.args())
        return ("name", line, v)

    def new_expr(self, line: int):
        t = self.peek()
        if t[0] != "id":
            raise PdeError("syntax", "expected a type after new", line)
        name = t[1]
        self.i += 1
        if name in PRIMS or name == "String" or (self.is_op("[") and name not in ("ArrayList",)):
            base = prim(name) if name in PRIMS else ("String" if name == "String" else ("C", name, ()))
            dims = []
            extra = 0
            while self.is_op("["):
                self.i += 1
                if self.accept("]"):
                    extra += 1
                    continue
                if extra:
                    raise PdeError("syntax", "array sizes must come first", line)
                dims.append(self.expr())
                self.expect("]")
            init = self.array_init() if self.is_op("{") else None
            if not dims and init is None:
                raise PdeError("syntax", "an array needs a size or {...}", line)
            return ("newarr", line, base, dims, extra, init)
        args: tuple = ()
        if self.is_op("<"):
            got = self.type_args()
            args = got if got is not None else ()
        while self.is_op(".") and self.is_id(k=1) and name[0].islower():
            self.i += 1
            name = self.ident()
            if self.is_op("<"):
                got = self.type_args()
                args = got if got is not None else ()
        return ("new", line, ("C", name, args), self.args())

    def postfix(self, e):
        while True:
            t = self.peek()
            if t[0] != "op":
                return e
            if t[1] == ".":
                self.i += 1
                name = self.ident()
                if self.is_op("("):
                    e = ("mcall", t[2], e, name, self.args())
                else:
                    e = ("field", t[2], e, name)
            elif t[1] == "[":
                self.i += 1
                idx = self.expr()
                self.expect("]")
                e = ("index", t[2], e, idx)
            elif t[1] in ("++", "--"):
                if e[0] not in ("name", "field", "index"):
                    return e
                self.i += 1
                e = ("post", t[2], t[1], e)
            else:
                return e


def _neg(kind: str, v):
    if kind == "int":
        return -v if v != 0x80000000 else -0x80000000
    return -v


def parse(src: str, classes=(), first_line: int = 1, bounded: bool = True) -> list:
    """`classes`: class names declared elsewhere (the art library's, for its own parts)."""
    return Parser(src, classes, first_line, bounded).program()
