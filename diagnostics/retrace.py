"""A small pure-Python R8/ProGuard retrace (API-013): turns an obfuscated Android stack trace back into the
original class, method, file and line names with the build's `mapping.txt`.

Mapping format (what R8 writes):

    # {"id":"com.android.tools.r8.mapping","version":"2.2"}
    com.arcu.attnd.ui.EventViewModel -> a.b.c:
    # {"id":"sourceFile","fileName":"EventViewModel.kt"}
        kotlinx.coroutines.flow.StateFlow state -> a              (a field: ignored)
        1:4:void refresh(boolean):40:43 -> b                      (minified lines 1-4 = original 40-43)
        5:5:void com.arcu.attnd.data.Repo.load():88:88 -> b       (inlined: innermost frame first …)
        5:5:void refresh(boolean):44 -> b                         (… then its caller, all at minified line 5)
        void unused() -> c                                        (no line information)

Line rules: an original range as long as the minified range maps line by line; a single original line (or
a range of another length) means every minified line maps to its first line; no original lines means the
minified line numbers are the original ones. Lines that match nothing fall back to the methods without
ranges, then to every method with that obfuscated name (ambiguous frames are listed with "<OR>").

Only the classes that appear in the stack are kept while reading the mapping, so a large file costs one
pass and little memory.
"""
import json
import re
from dataclasses import dataclass, field

CLASS_LINE = re.compile(r'^(\S+) -> (\S+):\s*$')
METHOD_LINE = re.compile(
    r'^\s+(?:(\d+):(\d+):)?(\S+) ([^\s(]+)\(([^)]*)\)(?::(\d+)(?::(\d+))?)? -> (\S+)\s*$')
FRAME = re.compile(r'^(?P<indent>\s*at\s+)(?P<cls>[\w$.]+)\.(?P<method>[\w$<>-]+)\((?P<source>[^)]*)\)(?P<rest>.*)$')
# "java.lang.IllegalStateException: boom", "Caused by: a.b: boom", 'Exception in thread "main" a.b.c'
EXCEPTION = re.compile(r'^(?P<prefix>\s*(?:Caused by: |Suppressed: |Exception in thread "[^"]*" )?)'
                       r'(?P<cls>[a-zA-Z_$][\w$]*(?:\.[\w$]+)+)(?P<rest>(?::.*)?)$')


@dataclass
class MethodMapping:
    original_class: str  # the class the original method is in (differs from the mapped class when inlined)
    name: str
    args: str
    obf_start: int | None
    obf_end: int | None
    orig_start: int | None
    orig_end: int | None

    def has_range(self):
        return self.obf_start is not None

    def covers(self, line):
        return self.has_range() and self.obf_start <= line <= self.obf_end

    def original_line(self, line):
        if line is None:
            return self.orig_start
        if not self.has_range():
            return self.orig_start  # no line information in the mapping: don't invent one
        if self.orig_start is None:
            return line
        if self.orig_end is not None and self.orig_end - self.orig_start == self.obf_end - self.obf_start:
            return self.orig_start + (line - self.obf_start)
        return self.orig_start


@dataclass
class ClassMapping:
    original: str
    obfuscated: str
    source_file: str = ''
    methods: dict = field(default_factory=dict)  # obfuscated name -> [MethodMapping] (mapping-file order)


class Mapping:
    """The parts of a mapping file needed for some obfuscated class names (all of them when `wanted` is None)."""

    def __init__(self, text, wanted=None):
        self.by_obfuscated = {}
        self.source_files = {}  # original class name -> file name, for every class in the file
        self.original_names = {}  # obfuscated -> original, for every class (exception lines)
        current = None
        keep = False
        for raw in text.splitlines():
            if not raw.strip():
                continue
            if raw.startswith('#'):
                if current is not None:
                    self._comment(raw, current)
                continue
            if not raw[0].isspace():
                match = CLASS_LINE.match(raw)
                if not match:
                    current, keep = None, False
                    continue
                original, obfuscated = match.groups()
                self.original_names[obfuscated] = original
                current = ClassMapping(original, obfuscated)
                keep = wanted is None or obfuscated in wanted
                if keep:
                    self.by_obfuscated[obfuscated] = current
                continue
            if current is None or not keep:
                continue
            match = METHOD_LINE.match(raw)
            if not match:
                continue  # a field, or a format we don't use
            obf_start, obf_end, _ret, name, args, orig_start, orig_end, obf_name = match.groups()
            if '.' in name:
                original_class, name = name.rsplit('.', 1)
            else:
                original_class = current.original
            current.methods.setdefault(obf_name, []).append(MethodMapping(
                original_class, name, args,
                int(obf_start) if obf_start else None, int(obf_end) if obf_end else None,
                int(orig_start) if orig_start else None, int(orig_end) if orig_end else None))

    def _comment(self, raw, current):
        try:
            data = json.loads(raw.lstrip('#').strip())
        except ValueError:
            return
        if isinstance(data, dict) and data.get('id') == 'sourceFile' and data.get('fileName'):
            self.source_files[current.original] = data['fileName']
            current.source_file = data['fileName']

    def file_for(self, original_class, fallback):
        if original_class in self.source_files:
            return self.source_files[original_class]
        outer = original_class.split('$', 1)[0]
        return self.source_files.get(outer, fallback)


def _parse_source(source):
    """'SourceFile:12' -> ('SourceFile', 12); 'Unknown Source' -> ('Unknown Source', None)."""
    name, _, line = source.rpartition(':')
    if name and line.isdigit():
        return name, int(line)
    return source, None


def _frames_for(cls, method, line):
    """The original frames for one obfuscated frame: [[frames of the first match], [alternative], …]."""
    candidates = cls.methods.get(method, [])
    if not candidates:
        return []
    if line is not None:
        hit = [m for m in candidates if m.covers(line)]
        if hit:
            # Consecutive entries with the same minified range are one inlined chain (innermost first).
            first = hit[0]
            chain = [m for m in hit if (m.obf_start, m.obf_end) == (first.obf_start, first.obf_end)]
            return [[(m, m.original_line(line)) for m in chain]]
    plain = [m for m in candidates if not m.has_range()] or candidates
    seen, options = set(), []
    for m in plain:
        key = (m.original_class, m.name)
        if key not in seen:
            seen.add(key)
            options.append([(m, m.original_line(line) if not m.has_range() else None)])
    return options


def retrace(stack, mapping_text):
    """The retraced stack trace text. Lines that aren't obfuscated frames are kept (class names mapped)."""
    lines = stack.splitlines()
    wanted = set()
    for line in lines:
        match = FRAME.match(line)
        if match:
            wanted.add(match['cls'])
    mapping = Mapping(mapping_text, wanted)
    out = []
    for line in lines:
        match = FRAME.match(line)
        if not match:
            exc = EXCEPTION.match(line)
            if exc and exc['cls'] in mapping.original_names:
                line = f"{exc['prefix']}{mapping.original_names[exc['cls']]}{exc['rest']}"
            out.append(line)
            continue
        cls = mapping.by_obfuscated.get(match['cls'])
        if cls is None:
            out.append(line)
            continue
        source_name, line_number = _parse_source(match['source'])
        options = _frames_for(cls, match['method'], line_number)
        if not options:
            out.append(f"{match['indent']}{cls.original}.{match['method']}({match['source']}){match['rest']}")
            continue
        for index, chain in enumerate(options):
            for method, original_line in chain:
                file_name = mapping.file_for(method.original_class, source_name)
                where = f'{file_name}:{original_line}' if original_line else file_name
                indent = match['indent'] if index == 0 else match['indent'].replace('at ', '<OR> at ', 1)
                out.append(f'{indent}{method.original_class}.{method.name}({where}){match["rest"]}')
    return '\n'.join(out)
