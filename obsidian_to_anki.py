#!/usr/bin/env python3
"""
obsidian_to_anki.py  –  Obsidian Markdown → Anki .apkg
========================================================

Jede Überschrift (H2–H5) wird eine eigene Karte:
  Vorderseite : Breadcrumb  +  Überschrift
  Rückseite   : Bullet-Liste darunter, sauber formatiert

Unterdecks:  <Deckname>::<Dateiname>

Verwendung
----------
    python obsidian_to_anki.py <ordner_oder_datei> [-d Deckname] [-o out.apkg]

API
---
    from obsidian_to_anki import convert_directory, convert_files
"""

import os, re, sys, json, time, hashlib, sqlite3, zipfile, tempfile, textwrap
from pathlib import Path
from typing import Optional

DEFAULT_OUTPUT    = "output.apkg"
DEFAULT_DECK_NAME = "Obsidian"

# ── PDF-Crop (pdf2image + PIL) ────────────────────────────────────────────────

_PDF_DEPS_OK = None   # None = noch nicht geprüft
_PDF_DPI     = 150    # Auflösung beim Rastern

def _check_pdf_deps() -> bool:
    global _PDF_DEPS_OK
    if _PDF_DEPS_OK is not None:
        return _PDF_DEPS_OK
    try:
        import pdf2image; from PIL import Image  # noqa
        _PDF_DEPS_OK = True
    except ImportError:
        print("\u26a0  pdf2image / Pillow nicht installiert \u2192 PDF-Snippets werden \xfcbersprungen.")
        print("   Installieren mit:  pip install pdf2image Pillow")
        _PDF_DEPS_OK = False
    return _PDF_DEPS_OK


def _crop_pdf_snippet(pdf_path, page: int, rect: tuple, out_dir) -> "Optional[Path]":
    """Rastert eine PDF-Seite und schneidet den rect-Ausschnitt aus.
    rect = (x0, y0, x1, y1) in PDF-Punkten (72-dpi-Basis), Y=0 unten."""
    if not _check_pdf_deps():
        return None
    if not pdf_path.exists():
        print(f"  \u26a0  PDF nicht gefunden: {pdf_path}")
        return None
    try:
        from pdf2image import convert_from_path
        from PIL import Image
        pages = convert_from_path(str(pdf_path), dpi=_PDF_DPI,
                                  first_page=page, last_page=page)
        if not pages:
            return None
        img = pages[0]
        scale = _PDF_DPI / 72.0
        x0, y0, x1, y1 = rect
        # PDF: y=0 unten  →  PIL: y=0 oben
        h      = img.height
        top    = max(0, int(h - y1 * scale))
        bottom = min(h, int(h - y0 * scale))
        left   = max(0, int(x0 * scale))
        right  = min(img.width, int(x1 * scale))
        if right <= left or bottom <= top:
            print(f"  \u26a0  Ung\xfcltiger rect-Ausschnitt: {pdf_path.name} S.{page}")
            return None
        cropped = img.crop((left, top, right, bottom))
        safe  = re.sub(r"[^\w]", "_", pdf_path.stem)
        fname = f"{safe}_p{page}_{int(x0)}-{int(y0)}-{int(x1)}-{int(y1)}.png"
        out   = out_dir / fname
        cropped.save(str(out), "PNG", optimize=True)
        return out
    except Exception as e:
        print(f"  \u26a0  PDF-Crop Fehler ({pdf_path.name} S.{page}): {e}")
        return None


# ── Obsidian-Syntax bereinigen ────────────────────────────────────────────────

def _clean(text: str) -> str:
    text = re.sub(r"!\[\[.*?\]\]", "", text)
    text = re.sub(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]", r"\1", text)
    text = re.sub(r"%%.*?%%", "", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\w)#\w+", "", text)
    return text.strip()


_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# Regex: Datei.pdf#page=N&rect=x0,y0,x1,y1
_PDF_SNIPPET_RE = re.compile(
    r"^(?P<file>.+?\.pdf)#page=(?P<page>\d+)&rect="
    r"(?P<x0>[\d.]+),(?P<y0>[\d.]+),(?P<x1>[\d.]+),(?P<y1>[\d.]+)",
    re.IGNORECASE,
)


def _find_file(name: str, vault_path) -> "Optional[Path]":
    """Sucht eine Datei im Vault: direkt, dann rekursiv."""
    if vault_path is None:
        return None
    direct = vault_path / name
    if direct.exists():
        return direct
    for found in sorted(vault_path.rglob(name)):
        return found
    return None


def _embed_images(text: str, vault_path, media_map: dict, tmp_dir) -> str:
    """Ersetzt alle ![[...]] durch <img> (Bilder) oder PDF-Crops."""
    def replace(m):
        raw = m.group(1).strip()
        ref = raw.split("|")[0].strip()

        # PDF-Snippet
        pm = _PDF_SNIPPET_RE.match(ref)
        if pm:
            pdf_name = pm.group("file")
            page     = int(pm.group("page"))
            rect     = (float(pm.group("x0")), float(pm.group("y0")),
                        float(pm.group("x1")), float(pm.group("y1")))
            pdf_path = _find_file(pdf_name, vault_path)
            if pdf_path is None:
                return (f'<span style="color:#c00;font-size:.85em">' +
                        f'[PDF nicht gefunden: {pdf_name}]</span>')
            out_png = _crop_pdf_snippet(pdf_path, page, rect, tmp_dir)
            if out_png is None:
                return (f'<span style="color:#c00;font-size:.85em">' +
                        f'[PDF-Crop fehlgeschlagen: {pdf_name} S.{page}]</span>')
            media_map[out_png.name] = out_png
            return (f'<img src="{out_png.name}" ' +
                    'style="max-width:100%;border-radius:4px;margin:8px 0;">')

        # Normales Bild
        if Path(ref).suffix.lower() in _IMG_EXTS:
            found = _find_file(ref, vault_path)
            if found:
                media_map[found.name] = found
                return (f'<img src="{found.name}" ' +
                        'style="max-width:100%;border-radius:4px;margin:8px 0;">')
            return (f'<span style="color:#c00;font-size:.85em">' +
                    f'[Bild nicht gefunden: {ref}]</span>')

        return ""  # Canvas, etc.

    return re.sub(r"!\[\[([^\]]+)\]\]", replace, text)
def _md_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*",     r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`",       r"<code>\1</code>", text)
    return text

# ── Bullets → verschachteltes HTML ───────────────────────────────────────────

def _bullets_to_html(bullets: list, vault_path=None, media_map=None, tmp_dir=None) -> str:
    if media_map is None: media_map = {}
    if tmp_dir is None: tmp_dir = Path(tempfile.mkdtemp())
    if not bullets:
        return ""

    def level(line: str) -> int:
        s = len(line) - len(line.lstrip("\t "))
        return line[:s].replace("\t", "    ").__len__() // 2

    base = level(bullets[0])
    out, depth = [], 0

    for raw in bullets:
        content = re.sub(r"^[-*+]\s*", "", raw.lstrip("\t "))
        content = _embed_images(content, vault_path, media_map, tmp_dir)
        content = _clean(content)
        content = _md_inline(content)
        if not content:
            continue
        cur = level(raw) - base
        while depth < cur:  out.append("<ul>"); depth += 1
        while depth > cur:  out.append("</ul>"); depth -= 1
        if depth == 0:      out.append("<ul>"); depth = 1
        out.append(f"<li>{content}</li>")

    while depth > 0: out.append("</ul>"); depth -= 1
    return "\n".join(out)

# ── Parser ────────────────────────────────────────────────────────────────────

class Block:
    def __init__(self, level: int, title: str, ancestors: list):
        self.level     = level
        self.title     = _clean(title)
        self.ancestors = ancestors   # [h2_title, h3_title, ...]  Eltern
        self.bullets: list = []


def parse_file(md_text: str, filename: str = "") -> list:
    """
    Parst eine Obsidian-Markdown-Datei.

    Bullets VOR dem ersten Heading werden einem Intro-Block mit dem
    Dateinamen als Titel zugeordnet, damit sie nicht verloren gehen.
    """
    blocks: list        = []
    current             = None
    ancestor_stack: list = []
    in_waypoint         = False

    # Intro-Block für Bullets vor dem ersten Heading
    # Wird nur dann zu blocks hinzugefügt, wenn er tatsächlich Bullets hat.
    intro_block = Block(level=0, title=filename or "Übersicht", ancestors=[])

    for raw in md_text.splitlines():
        line = raw.rstrip()

        # Waypoint ignorieren
        if "%% Begin Waypoint %%" in line: in_waypoint = True;  continue
        if "%% End Waypoint %%"   in line: in_waypoint = False; continue
        if in_waypoint: continue

        stripped = line.strip()
        if not stripped: continue

        # Reine Tag-Zeile (#Tag #Tag2)
        if re.match(r"^(#\w+\s*)+$", stripped): continue

        # ── Echter Heading  ### Titel ──────────────────────────────────────
        hm = re.match(r"^(#{2,6})\s+(.+)", line)
        # ── Als Bullet geschriebener Heading  - #### Titel ────────────────
        bh = re.match(r"^\s*[-*+]\s+(#{3,6})\s+(.+)", line) if not hm else None

        if hm or bh:
            # Beim ersten echten Heading: Intro-Block eintragen falls er Inhalt hat
            if current is None and intro_block.bullets:
                blocks.append(intro_block)

            hashes    = (hm or bh).group(1)
            raw_title = (hm or bh).group(2)
            lvl       = len(hashes)

            ancestor_stack = [(l, t) for l, t in ancestor_stack if l < lvl]
            ancestors      = [t for _, t in ancestor_stack]

            block = Block(lvl, raw_title, ancestors)
            blocks.append(block)
            current = block
            ancestor_stack.append((lvl, block.title))
            continue

        # ── Bullet, Einrückung oder bare Inhaltszeile (z.B. ![[...]])  ───────
        if re.match(r"^\s*[-*+]\s", line) or re.match(r"^\t| {2,}", line):
            # Normal bullet or indented continuation
            if current is not None:
                current.bullets.append(line)
            else:
                intro_block.bullets.append(line)
        else:
            # Bare content line (e.g. a standalone ![[img]] or plain text,
            # not a heading, not a bullet) — wrap as a pseudo-bullet so it
            # renders correctly and isn't silently dropped.
            pseudo = f"- {line.strip()}"
            if current is not None:
                current.bullets.append(pseudo)
            else:
                intro_block.bullets.append(pseudo)

    # Datei hat gar keine Headings → Intro-Block ist der einzige Block
    if current is None and intro_block.bullets:
        blocks.append(intro_block)

    return blocks

# ── Karten bauen ─────────────────────────────────────────────────────────────

CSS = textwrap.dedent("""\
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');

    .card {
        font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
        font-size: 15px;
        line-height: 1.65;
        color: #1c1c1e;
        background: #ffffff;
        padding: 0;
        max-width: 720px;
        margin: 0 auto;
    }

    /* ── Kopfzeile (Breadcrumb + Titel) ── */
    .card-header {
        background: #f2f5ff;
        border-bottom: 2px solid #d0d9ff;
        padding: 12px 18px 10px;
    }
    .breadcrumb {
        font-size: 0.75em;
        color: #5566aa;
        letter-spacing: .03em;
        margin-bottom: 4px;
        display: flex;
        flex-wrap: wrap;
        gap: 2px;
        align-items: center;
    }
    .breadcrumb .sep { color: #aab; margin: 0 2px; }
    .breadcrumb .crumb {
        background: #e0e7ff;
        border-radius: 4px;
        padding: 1px 7px;
        font-weight: 600;
    }
    .card-title {
        font-size: 1.15em;
        font-weight: 700;
        color: #1a3a8f;
        margin: 0;
    }

    /* ── Rückseite ── */
    .card-body {
        padding: 14px 18px 16px;
    }
    ul {
        margin: 4px 0 4px 0;
        padding-left: 1.3em;
    }
    li {
        margin: 4px 0;
    }
    ul ul {
        margin: 2px 0;
        padding-left: 1.2em;
    }
    b  { color: #1a3a8f; }
    em { color: #444; }
    code {
        background: #f0f0f5;
        border-radius: 3px;
        padding: 1px 5px;
        font-size: 0.88em;
        font-family: monospace;
    }
    hr { border: none; border-top: 1px solid #e0e5f0; margin: 10px 0; }

    /* Night mode */
    .night_mode .card        { background: #1e1e2e; color: #cdd6f4; }
    .night_mode .card-header { background: #1e2050; border-color: #3344aa; }
    .night_mode .breadcrumb  { color: #89b4fa; }
    .night_mode .breadcrumb .crumb { background: #2a2f6a; }
    .night_mode .card-title  { color: #89b4fa; }
    .night_mode b            { color: #89b4fa; }
    .night_mode code         { background: #2a2a3e; }
""")

FRONT_TMPL = "{{Front}}"
BACK_TMPL  = "{{FrontSide}}<hr>{{Back}}"


def _make_header(filename: str, ancestors: list, title: str) -> str:
    """Baut die Kopfzeile: Breadcrumb-Badges + fetter Titel."""
    parts = [filename] + ancestors
    crumbs = "".join(
        f'<span class="crumb">{_md_inline(_clean(p))}</span>'
        f'<span class="sep">›</span>'
        for p in parts
    )
    return (
        f'<div class="card-header">'
        f'  <div class="breadcrumb">{crumbs}</div>'
        f'  <div class="card-title">{_md_inline(title)}</div>'
        f'</div>'
    )


def blocks_to_cards(blocks: list, filename: str,
                    vault_path=None, media_map=None, tmp_dir=None) -> list:
    if media_map is None: media_map = {}
    if tmp_dir is None: tmp_dir = Path(tempfile.mkdtemp())
    cards = []
    for b in blocks:
        if not b.bullets:
            continue
        front = _make_header(filename, b.ancestors, b.title)
        back  = f'<div class="card-body">{_bullets_to_html(b.bullets, vault_path, media_map, tmp_dir)}</div>'
        cards.append((front, back))
    return cards


def extract_cards(md_text: str, filename: str = "Notiz",
                  vault_path=None, tmp_dir=None) -> tuple:
    """Gibt (cards, media_map) zurück.
    media_map = {anki_filename: Path} für alle gefundenen Bilder und PDF-Crops."""
    media_map = {}
    if tmp_dir is None: tmp_dir = Path(tempfile.mkdtemp())
    cards = blocks_to_cards(parse_file(md_text, filename), filename,
                            vault_path=vault_path, media_map=media_map,
                            tmp_dir=tmp_dir)
    return cards, media_map

# ── .apkg bauen ──────────────────────────────────────────────────────────────

def _uid(*parts: str) -> int:
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16)


def build_apkg(deck_cards: dict, root_deck: str, output_path: str,
               media_files: dict = None) -> None:
    if media_files is None: media_files = {}
    total = sum(len(v) for v in deck_cards.values())
    if not total:
        print("⚠  Keine Karten gefunden.")
        return

    model_id = _uid(root_deck, "model_v3")
    now      = int(time.time())

    # Decks (inkl. Eltern-Decks für verschachtelte Namen)
    decks_meta = {}
    def ensure(name):
        parts = name.split("::")
        for i in range(1, len(parts)+1):
            full = "::".join(parts[:i])
            if full not in decks_meta:
                decks_meta[full] = {
                    "id": _uid("deck", full), "name": full, "conf": 1,
                    "extendRev": 50, "extendNew": 10,
                    "collapsed": False, "browserCollapsed": False,
                    "desc": "", "dyn": 0, "mod": now, "usn": -1,
                    "newToday": [0,0], "revToday": [0,0],
                    "lrnToday": [0,0], "timeToday": [0,0],
                }
    ensure("Default")
    for name in deck_cards: ensure(name)
    decks_json = {str(v["id"]): v for v in decks_meta.values()}
    decks_json["1"] = {**decks_meta["Default"], "id": 1}

    model = { str(model_id): {
        "id": str(model_id), "name": "Obsidian Simple", "type": 0,
        "mod": now, "usn": -1, "sortf": 0, "did": 1,
        "tmpls": [{"name":"Card 1","ord":0,
                   "qfmt": FRONT_TMPL, "afmt": BACK_TMPL,
                   "bqfmt":"","bafmt":"","did":None,"bfont":"","bsize":0}],
        "flds": [
            {"name":"Front","ord":0,"sticky":False,"rtl":False,"font":"Arial","size":20,"media":[]},
            {"name":"Back", "ord":1,"sticky":False,"rtl":False,"font":"Arial","size":20,"media":[]},
        ],
        "css": CSS, "latexPre":"","latexPost":"","vers":[],"tags":[],
    }}

    dconf = {"1": {
        "id":1,"name":"Default","replayq":True,"timer":0,"maxTaken":60,
        "usn":0,"autoplay":True,"mod":now,
        "lapse":{"leechFails":8,"minInt":1,"delays":[10],"leechAction":0,"mult":0},
        "rev":{"perDay":200,"ease4":1.3,"fuzz":0.05,"minSpace":1,
               "ivlFct":1,"maxIvl":36500,"bury":True,"hardFactor":1.2},
        "new":{"perDay":30,"delays":[1,10],"separate":True,"ints":[1,4,7],
               "initialFactor":2500,"bury":True,"order":1},
    }}

    col_conf = json.dumps({
        "nextPos":1,"estTimes":True,"activeDecks":[1],"sortType":"noteFld",
        "timeLim":0,"sortBackwards":False,"addToCur":True,"curDeck":1,
        "newBury":True,"newSpread":0,"dueCounts":True,
        "curModel":str(model_id),"collapseTime":1200,
    })

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "collection.anki2")
        con = sqlite3.connect(db)
        cur = con.cursor()
        cur.executescript("""
            CREATE TABLE col(id INTEGER PRIMARY KEY,crt INTEGER NOT NULL,
              mod INTEGER NOT NULL,scm INTEGER NOT NULL,ver INTEGER NOT NULL,
              dty INTEGER NOT NULL,usn INTEGER NOT NULL,ls INTEGER NOT NULL,
              conf TEXT NOT NULL,models TEXT NOT NULL,decks TEXT NOT NULL,
              dconf TEXT NOT NULL,tags TEXT NOT NULL);
            CREATE TABLE notes(id INTEGER PRIMARY KEY,guid TEXT NOT NULL,
              mid INTEGER NOT NULL,mod INTEGER NOT NULL,usn INTEGER NOT NULL,
              tags TEXT NOT NULL,flds TEXT NOT NULL,sfld TEXT NOT NULL,
              csum INTEGER NOT NULL,flags INTEGER NOT NULL,data TEXT NOT NULL);
            CREATE TABLE cards(id INTEGER PRIMARY KEY,nid INTEGER NOT NULL,
              did INTEGER NOT NULL,ord INTEGER NOT NULL,mod INTEGER NOT NULL,
              usn INTEGER NOT NULL,type INTEGER NOT NULL,queue INTEGER NOT NULL,
              due INTEGER NOT NULL,ivl INTEGER NOT NULL,factor INTEGER NOT NULL,
              reps INTEGER NOT NULL,lapses INTEGER NOT NULL,left INTEGER NOT NULL,
              odue INTEGER NOT NULL,odid INTEGER NOT NULL,flags INTEGER NOT NULL,
              data TEXT NOT NULL);
            CREATE TABLE revlog(id INTEGER PRIMARY KEY,cid INTEGER NOT NULL,
              usn INTEGER NOT NULL,ease INTEGER NOT NULL,ivl INTEGER NOT NULL,
              lastIvl INTEGER NOT NULL,factor INTEGER NOT NULL,
              time INTEGER NOT NULL,type INTEGER NOT NULL);
            CREATE TABLE graves(usn INTEGER NOT NULL,oid INTEGER NOT NULL,
              type INTEGER NOT NULL);
            CREATE INDEX ix_notes_usn   ON notes(usn);
            CREATE INDEX ix_cards_usn   ON cards(usn);
            CREATE INDEX ix_cards_nid   ON cards(nid);
            CREATE INDEX ix_cards_sched ON cards(did,queue,due);
            CREATE INDEX ix_revlog_usn  ON revlog(usn);
            CREATE INDEX ix_revlog_cid  ON revlog(cid);
        """)
        cur.execute("INSERT INTO col VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1,now,now,now,11,0,-1,0,col_conf,
             json.dumps(model),json.dumps(decks_json),json.dumps(dconf),"{}"))

        n = 0
        for subdeck, card_list in deck_cards.items():
            did = 1 if subdeck=="Default" else decks_meta[subdeck]["id"]
            for front, back in card_list:
                nid = _uid(subdeck, str(n), front[:60]) + n
                cid = nid + 500_000
                flds = f"{front}\x1f{back}"
                sfld = re.sub("<[^>]+>","",front)[:120]
                csum = int(hashlib.sha1(sfld.encode()).hexdigest()[:8],16)
                cur.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (nid,f"obs_{n}",model_id,now,-1,"",flds,sfld,csum,0,""))
                cur.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cid,nid,did,0,now,-1,0,0,n,0,0,0,0,0,0,0,0,""))
                n += 1

        con.commit(); con.close()
        with zipfile.ZipFile(output_path,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.write(db,"collection.anki2")
            # Mediendateien einbetten
            media_index = {}
            for i, (anki_name, src_path) in enumerate(media_files.items()):
                if src_path.exists():
                    zf.write(str(src_path), str(i))
                    media_index[str(i)] = anki_name
                else:
                    print(f"  ⚠  Bild nicht gefunden (übersprungen): {src_path}")
            zf.writestr("media", json.dumps(media_index))

    print(f"✅  {n} Karten in {len(deck_cards)} Unterdeck(s)  →  {output_path}")
    for name, cards in deck_cards.items():
        print(f"   • {name}: {len(cards)} Karten")

# ── Öffentliche API ───────────────────────────────────────────────────────────

def _stem(path: Path) -> str:
    return re.sub(r"_+", " ", path.stem).strip()


def _deck_name_for(file: Path, source_root: Path, deck_name: str, flat: bool,
                   is_single_file: bool = False) -> str:
    """
    Baut den vollständig verschachtelten Deck-Namen aus der Ordnerstruktur.

    Ordner-Quelle:
        source_root = /vault/Biochemie
        file        = /vault/Biochemie/Hormone/Regulation/Thyroxin.md
        → "Medizin::Hormone::Regulation::Thyroxin"

    Einzeldatei (is_single_file=True):
        file        = /irgendwo/Thyroxin.md
        → "Medizin"   (direkt ins Root-Deck, kein Extra-Unterdeck)
    """
    if flat:
        return deck_name
    if is_single_file:
        return f"{deck_name}::{_stem(file)}" if deck_name else _stem(file)

    try:
        rel = file.relative_to(source_root)
    except ValueError:
        rel = Path(file.name)

    # Ordner-Teile + Dateiname, jeweils bereinigt
    folder_parts = [re.sub(r"_+", " ", p).strip() for p in rel.parts[:-1]]
    file_part    = _stem(file)
    all_parts    = folder_parts + [file_part]

    return "::".join([deck_name] + all_parts) if deck_name else "::".join(all_parts)


def _run(paths: list, output_path: str, deck_name: str, flat: bool,
         vault_path=None,
         source_roots: "dict[Path, Path] | None" = None,
         single_files: "set[Path] | None" = None):
    """
    source_roots:  Map {file_path → source_root} für Deck-Hierarchie.
    single_files:  Set von Dateien die direkt ins Root-Deck sollen (keine Unterdeck-Ebene).
    """
    deck_cards: dict = {}
    all_media:  dict = {}

    with tempfile.TemporaryDirectory(prefix="obsidian_anki_") as _tmp:
        tmp_dir = Path(_tmp)
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            stem = _stem(path)
            vp   = vault_path or path.parent

            # Welcher Source-Root gehört zu dieser Datei?
            root = (source_roots or {}).get(path, path.parent)

            cards, media_map = extract_cards(text, stem, vault_path=vp, tmp_dir=tmp_dir)
            all_media.update(media_map)
            if not cards:
                print(f"  {path.name}: 0 Karten (übersprungen)")
                continue

            is_single = (single_files or set()).__contains__(path)
            sub = _deck_name_for(path, root, deck_name, flat, is_single_file=is_single)
            deck_cards.setdefault(sub, []).extend(cards)

            imgs  = sum(1 for p in media_map.values() if p.suffix == ".png" and "_p" in p.stem)
            pics  = len(media_map) - imgs
            parts = []
            if pics: parts.append(f"{pics} Bild(er)")
            if imgs: parts.append(f"{imgs} PDF-Crop(s)")
            extra = f", {', '.join(parts)}" if parts else ""
            print(f"  {path.name}: {len(cards)} Karten{extra}  →  {sub}")

        build_apkg(deck_cards, deck_name, output_path, media_files=all_media)

def convert_directory(input_dir: str, output_path=DEFAULT_OUTPUT,
                      deck_name=DEFAULT_DECK_NAME, flat=False,
                      vault_path: str = None):
    """
    Verarbeitet alle .md-Dateien in input_dir rekursiv.
    Die Ordnerstruktur unterhalb von input_dir wird als Deck-Hierarchie übernommen.

    Beispiel:
        input_dir = /vault/Biochemie,  deck_name = Medizin
        /vault/Biochemie/Hormone/Thyroxin.md  →  Medizin::Hormone::Thyroxin
    """
    root  = Path(input_dir)
    files = sorted(root.rglob("*.md"))
    if not files:
        print(f"⚠  Keine .md-Dateien in '{input_dir}'.")
        return
    vp           = Path(vault_path) if vault_path else root
    source_roots = {f: root for f in files}
    _run(files, output_path, deck_name, flat,
         vault_path=vp, source_roots=source_roots)


def convert_files(file_paths: list, output_path=DEFAULT_OUTPUT,
                  deck_name=DEFAULT_DECK_NAME, flat=False,
                  vault_path: str = None):
    """
    Verarbeitet eine explizite Liste von Pfaden.
    Ordner darin werden rekursiv expandiert und deren interne
    Struktur als Deck-Hierarchie verwendet.
    Einzelne Dateien landen direkt unter dem Root-Deck (kein Dateiname als Unterdeck).
    """
    paths        = []
    source_roots = {}
    single_files: set = set()   # Dateien die direkt ins Root-Deck sollen
    for raw in file_paths:
        p = Path(raw)
        if not p.exists():
            print(f"⚠  Nicht gefunden: {raw}")
            continue
        if p.is_dir():
            for f in sorted(p.rglob("*.md")):
                paths.append(f)
                source_roots[f] = p          # Ordner = Root → Unterstruktur erhalten
        else:
            paths.append(p)
            source_roots[p] = p.parent
            single_files.add(p)              # Einzeldatei → direkt ins Root-Deck
    if not paths:
        return
    vp = Path(vault_path) if vault_path else None
    _run(paths, output_path, deck_name, flat,
         vault_path=vp, source_roots=source_roots, single_files=single_files)

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Obsidian Markdown → Anki .apkg")
    ap.add_argument("input", nargs="?", default=".",
                    help="Ordner oder einzelne .md-Datei (Standard: .)")
    ap.add_argument("-o","--output",  default=DEFAULT_OUTPUT)
    ap.add_argument("-d","--deck",    default=None,
                    help="Deckname (Standard: Ordner-/Dateiname)")
    ap.add_argument("--flat", action="store_true",
                    help="Kein Unterdeck pro Datei")
    ap.add_argument("--vault", default=None,
                    help="Pfad zum Obsidian-Vault-Root für Bildsuche "
                         "(Standard: gleicher Ordner wie die .md-Dateien)")
    args = ap.parse_args()
    inp  = Path(args.input)
    name = args.deck or (inp.stem if inp.is_file() else inp.resolve().name)
    if   inp.is_file(): convert_files([str(inp)], args.output, name, args.flat,
                                      vault_path=args.vault)
    elif inp.is_dir():  convert_directory(str(inp), args.output, name, args.flat,
                                          vault_path=args.vault)
    else: print(f"❌  '{args.input}' nicht gefunden."); sys.exit(1)
