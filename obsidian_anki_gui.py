#!/usr/bin/env python3
"""
obsidian_anki_gui.py  –  Tkinter GUI für obsidian_to_anki.py
Lege beide Dateien in denselben Ordner, dann:  python obsidian_anki_gui.py
"""

import sys, os, threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

# ── Converter importieren ─────────────────────────────────────────────────────
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
try:
    import obsidian_to_anki as converter
    CONV_OK = True
except ImportError as e:
    CONV_OK = False
    CONV_ERR = str(e)


# ══════════════════════════════════════════════════════════════════════════════
#  BACKEND BRIDGE
# ══════════════════════════════════════════════════════════════════════════════

class AnkiBackend:
    @staticmethod
    def export(sources, deck_name, output_file, vault_path, flat,
               on_log=None, on_done=None):
        """
        Runs the conversion in a background thread.
        Calls on_log(str) for progress lines and on_done(ok, msg) when finished.
        """
        def run():
            if not CONV_OK:
                if on_done: on_done(False, f"obsidian_to_anki.py not found:\n{CONV_ERR}")
                return
            try:
                import io, contextlib
                all_paths    = []
                source_roots = {}          # file → root-Ordner (für Deck-Hierarchie)
                vp = Path(vault_path) if vault_path else None

                single_files = set()   # files that go directly into the root deck

                for src in sources:
                    # resolve() normalises separators, symlinks and trailing slashes
                    # so is_dir() / is_file() always work correctly cross-platform
                    p = Path(src).resolve()
                    if p.is_dir():
                        files = sorted(p.rglob("*.md"))
                        all_paths.extend(files)
                        for f in files:
                            source_roots[f] = p
                        if on_log: on_log(f"📂 {p.name}  ({len(files)} files)")
                        if vp is None: vp = p
                    elif p.is_file():
                        all_paths.append(p)
                        source_roots[p] = p.parent
                        single_files.add(p)
                        if on_log: on_log(f"📄 {p.name}")
                        if vp is None: vp = p.parent
                    else:
                        if on_log: on_log(f"⚠  Not found: {src}")

                if not all_paths:
                    if on_done: on_done(False, "No .md files found.")
                    return

                if on_log: on_log(f"\n Processing {len(all_paths)} file(s)…\n")

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    converter._run(
                        paths=all_paths,
                        output_path=output_file,
                        deck_name=deck_name,
                        flat=flat,
                        vault_path=vp,
                        source_roots=source_roots,
                        single_files=single_files,
                    )

                for line in buf.getvalue().splitlines():
                    if on_log: on_log(line)

                if on_done: on_done(True, output_file)

            except Exception as e:
                import traceback
                if on_log: on_log(traceback.format_exc())
                if on_done: on_done(False, str(e))

        threading.Thread(target=run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  UI
# ══════════════════════════════════════════════════════════════════════════════

class AnkiUI(tk.Tk):
    # ── Palette (iOS-inspired, same feel as the reference) ─────────────────
    BG_APP    = "#F5F5F7"
    BG_CARD   = "#FFFFFF"
    BG_INPUT  = "#F2F2F7"
    TEXT_MAIN = "#1D1D1F"
    TEXT_MUTE = "#86868B"
    ACCENT    = "#5E5CE6"
    ACCENT_HV = "#403EAD"
    DANGER    = "#FF3B30"
    GREEN     = "#34C759"
    BORDER    = "#E5E5EA"

    def __init__(self):
        super().__init__()
        self.title("Obsidian → Anki")
        self.geometry("820x680")
        self.minsize(640, 520)
        self.configure(bg=self.BG_APP)
        self.resizable(True, True)

        self.sources: list[str] = []
        self._build_ui()
        self._bind_events()

    # ── Layout ───────────────────────────────────────────────────────────────
    def _build_ui(self):
        wrap = tk.Frame(self, bg=self.BG_APP)
        wrap.pack(fill=tk.BOTH, expand=True, padx=56, pady=36)

        # Header
        hdr = tk.Frame(wrap, bg=self.BG_APP)
        hdr.pack(fill=tk.X, pady=(0, 24))
        tk.Label(hdr, text="Obsidian  →  Anki", bg=self.BG_APP, fg=self.TEXT_MAIN,
                 font=("Helvetica", 26, "bold")).pack(anchor="w")
        tk.Label(hdr, text="Select notes, configure settings, export your deck.",
                 bg=self.BG_APP, fg=self.TEXT_MUTE,
                 font=("Helvetica", 12)).pack(anchor="w", pady=(3, 0))

        # ── Card ─────────────────────────────────────────────────────────────
        card = tk.Frame(wrap, bg=self.BG_CARD,
                        highlightthickness=1, highlightbackground=self.BORDER)
        card.pack(fill=tk.BOTH, expand=True)
        inner = tk.Frame(card, bg=self.BG_CARD, padx=36, pady=28)
        inner.pack(fill=tk.BOTH, expand=True)

        # ── Row 1: Vault + Deck Name ──────────────────────────────────────
        row1 = tk.Frame(inner, bg=self.BG_CARD)
        row1.pack(fill=tk.X, pady=(0, 18))

        vf = tk.Frame(row1, bg=self.BG_CARD)
        vf.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 14))
        self.vault_var = self._input_field(vf, "Vault Path (for images & PDFs)",
                                           btn_text="Browse",
                                           btn_cmd=self._browse_vault,
                                           placeholder="Leave empty = auto-detect")

        df = tk.Frame(row1, bg=self.BG_CARD)
        df.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.deck_var = self._input_field(df, "Deck Name", placeholder="z.B. Biochemie")
        self.deck_var.set("Obsidian")

        # ── Row 2: Output file ───────────────────────────────────────────
        row2 = tk.Frame(inner, bg=self.BG_CARD)
        row2.pack(fill=tk.X, pady=(0, 22))
        of = tk.Frame(row2, bg=self.BG_CARD)
        of.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 14))
        self.output_var = self._input_field(of, "Output File (.apkg)",
                                            btn_text="Save as",
                                            btn_cmd=self._browse_output)
        self.output_var.set(str(Path.home() / "output.apkg"))

        # Flat-deck toggle
        ff = tk.Frame(row2, bg=self.BG_CARD)
        ff.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(ff, text="Options", bg=self.BG_CARD, fg=self.TEXT_MUTE,
                 font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(0, 6))
        self.flat_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(ff, text="Flat deck (no sub-decks)",
                            variable=self.flat_var,
                            bg=self.BG_CARD, fg=self.TEXT_MAIN,
                            font=("Helvetica", 11),
                            activebackground=self.BG_CARD,
                            selectcolor=self.BG_CARD,
                            relief="flat", bd=0, cursor="hand2")
        cb.pack(anchor="w")

        # ── Source list header ───────────────────────────────────────────
        sh = tk.Frame(inner, bg=self.BG_CARD)
        sh.pack(fill=tk.X, pady=(0, 8))
        tk.Label(sh, text="Sources", bg=self.BG_CARD, fg=self.TEXT_MAIN,
                 font=("Helvetica", 14, "bold")).pack(side=tk.LEFT)
        self._txt_btn(sh, "✕  Remove", self._remove_selected, self.DANGER).pack(side=tk.RIGHT)
        self._txt_btn(sh, "+ Folder",     self._add_folder,       self.ACCENT).pack(side=tk.RIGHT, padx=14)
        # Hint label under source header
        self._txt_btn(sh, "+ Files",    self._add_files,        self.ACCENT).pack(side=tk.RIGHT)

        # ── Source listbox ───────────────────────────────────────────────
        lw = tk.Frame(inner, bg=self.BG_INPUT, padx=2, pady=2)
        lw.pack(fill=tk.BOTH, expand=True)
        self.src_list = tk.Listbox(
            lw, bg=self.BG_CARD, fg=self.TEXT_MAIN,
            selectbackground=self.ACCENT, selectforeground="#FFFFFF",
            relief="flat", borderwidth=0, font=("Helvetica", 11),
            selectmode=tk.EXTENDED, activestyle="none",
            highlightthickness=0
        )
        sb = tk.Scrollbar(lw, orient=tk.VERTICAL, command=self.src_list.yview)
        self.src_list.configure(yscrollcommand=sb.set)
        self.src_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        sb.pack(side=tk.RIGHT, fill=tk.Y, pady=4)

        # ── Log area (collapsible) ────────────────────────────────────────
        self.log_frame = tk.Frame(inner, bg=self.BG_CARD)
        # not packed yet – shown on first export

        self.log_text = tk.Text(
            self.log_frame, height=7,
            bg=self.BG_INPUT, fg=self.TEXT_MUTE,
            font=("Courier", 10), relief="flat",
            bd=0, highlightthickness=0,
            state="disabled", wrap="word"
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        # colour tags
        self.log_text.tag_config("ok",   foreground=self.GREEN)
        self.log_text.tag_config("err",  foreground=self.DANGER)
        self.log_text.tag_config("warn", foreground="#FF9F0A")
        self.log_text.tag_config("info", foreground=self.ACCENT)

        # ── Footer ────────────────────────────────────────────────────────
        foot = tk.Frame(wrap, bg=self.BG_APP)
        foot.pack(fill=tk.X, pady=(16, 0))

        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(foot, textvariable=self.status_var,
                 bg=self.BG_APP, fg=self.TEXT_MUTE,
                 font=("Helvetica", 10)).pack(side=tk.LEFT, pady=8)

        self.btn_export = tk.Button(
            foot, text="Export Deck",
            command=self._export,
            bg=self.ACCENT, fg="#FFFFFF",
            font=("Helvetica", 12, "bold"),
            relief="flat", padx=28, pady=11,
            cursor="hand2",
            activebackground=self.ACCENT_HV,
            activeforeground="#FFFFFF"
        )
        self.btn_export.pack(side=tk.RIGHT)

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _input_field(self, parent, label, btn_text=None, btn_cmd=None, placeholder=""):
        tk.Label(parent, text=label, bg=self.BG_CARD, fg=self.TEXT_MUTE,
                 font=("Helvetica", 9, "bold")).pack(anchor="w", pady=(0, 5))
        frm = tk.Frame(parent, bg=self.BG_INPUT, padx=10, pady=7)
        frm.pack(fill=tk.X)
        var = tk.StringVar()
        ent = tk.Entry(frm, textvariable=var, bg=self.BG_INPUT, fg=self.TEXT_MAIN,
                       relief="flat", insertbackground=self.ACCENT,
                       font=("Helvetica", 11))
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if placeholder and not var.get():
            # soft placeholder via fg trick
            ent.insert(0, placeholder)
            ent.config(fg=self.TEXT_MUTE)
            def _on_focus_in(e):
                if ent.get() == placeholder:
                    ent.delete(0, tk.END)
                    ent.config(fg=self.TEXT_MAIN)
            def _on_focus_out(e):
                if not ent.get():
                    ent.insert(0, placeholder)
                    ent.config(fg=self.TEXT_MUTE)
            ent.bind("<FocusIn>",  _on_focus_in)
            ent.bind("<FocusOut>", _on_focus_out)
        if btn_text and btn_cmd:
            tk.Button(frm, text=btn_text, command=btn_cmd,
                      bg=self.BG_INPUT, fg=self.ACCENT,
                      relief="flat", font=("Helvetica", 9, "bold"),
                      cursor="hand2",
                      activebackground=self.BG_INPUT,
                      activeforeground=self.ACCENT_HV).pack(side=tk.RIGHT)
        return var

    def _txt_btn(self, parent, text, cmd, color):
        return tk.Button(parent, text=text, command=cmd,
                         bg=self.BG_CARD, fg=color,
                         relief="flat", font=("Helvetica", 10, "bold"),
                         cursor="hand2",
                         activebackground=self.BG_CARD,
                         activeforeground=color)

    def _bind_events(self):
        ctx = tk.Menu(self, tearoff=0, bg=self.BG_CARD, font=("Helvetica", 10))
        ctx.add_command(label="Remove",       command=self._remove_selected)
        ctx.add_separator()
        ctx.add_command(label="Remove all",  command=self._clear_all)
        self.src_list.bind("<Button-3>", lambda e: ctx.tk_popup(e.x_root, e.y_root))
        self.src_list.bind("<Delete>",   lambda e: self._remove_selected())
        self.bind_all("<Control-e>",     lambda e: self._export())

    # ── Source management ─────────────────────────────────────────────────────
    def _add_to_list(self, path: str):
        if path not in self.sources:
            self.sources.append(path)
            p = Path(path)
            icon = "📁" if p.is_dir() else "📄"
            self.src_list.insert(tk.END, f"  {icon}  {path}")

    def _add_files(self):
        # tk.splitlist ensures correct parsing on all platforms (Windows backslashes etc.)
        raw = filedialog.askopenfilenames(
            title="Select Markdown files",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")]
        )
        paths = self.tk.splitlist(raw) if isinstance(raw, str) else raw
        for p in paths:
            p = str(p).strip()
            if p:
                self._add_to_list(p)

    def _add_folder(self):
        """
        Opens askdirectory. Call multiple times to add multiple folders — 
        each click adds one folder. Duplicates are ignored automatically.
        """
        path = filedialog.askdirectory(title="Select folder to add")
        if path:
            self._add_to_list(str(path).strip())

    def _remove_selected(self):
        for i in reversed(self.src_list.curselection()):
            self.src_list.delete(i)
            self.sources.pop(i)

    def _clear_all(self):
        self.src_list.delete(0, tk.END)
        self.sources.clear()

    # ── Browse dialogs ────────────────────────────────────────────────────────
    def _browse_vault(self):
        path = filedialog.askdirectory(title="Select Vault Folder")
        if path: self.vault_var.set(path)

    def _browse_output(self):
        deck = self.deck_var.get() or "output"
        path = filedialog.asksaveasfilename(
            initialfile=f"{deck}.apkg",
            defaultextension=".apkg",
            filetypes=[("Anki Package", "*.apkg")]
        )
        if path: self.output_var.set(path)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _show_log(self):
        self.log_frame.pack(fill=tk.BOTH, expand=False, pady=(14, 0))

    def _log(self, line: str):
        """Append a line to the log widget (thread-safe via after)."""
        def _append():
            self.log_text.config(state="normal")
            tag = "ok"   if line.startswith("✅") else \
                  "err"  if ("❌" in line or "Error" in line or "Fehler" in line) else \
                  "warn" if line.startswith("⚠") else \
                  "info" if line.startswith("  •") else None
            self.log_text.insert(tk.END, line + "\n", tag)
            self.log_text.see(tk.END)
            self.log_text.config(state="disabled")
        self.after(0, _append)

    # ── Export ────────────────────────────────────────────────────────────────
    def _export(self):
        if not self.sources:
            messagebox.showwarning("No Sources", "Please add at least one folder or file.")
            return

        deck   = self.deck_var.get().strip()   or "Obsidian"
        output = self.output_var.get().strip()
        vault  = self.vault_var.get().strip()
        flat   = self.flat_var.get()

        # Validate / strip placeholder text
        VAULT_PH  = "Leave empty = auto-detect"
        if vault == VAULT_PH: vault = ""
        if not output or not output.endswith(".apkg"):
            messagebox.showwarning("Output File", "Please specify an .apkg output file.")
            return

        # UI → busy state
        self.btn_export.config(state="disabled", text="Exporting…")
        self.status_var.set("Processing notes…")
        self._show_log()

        # Clear log
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

        def on_done(ok: bool, msg: str):
            def _ui():
                self.btn_export.config(state="normal", text="Export Deck")
                if ok:
                    self.status_var.set(f"✅  Done → {Path(msg).name}")
                    messagebox.showinfo(
                        "Export Successful",
                        f"Deck saved successfully:\n{msg}"
                    )
                else:
                    self.status_var.set("❌  Export failed.")
                    messagebox.showerror("Export Failed", msg)
            self.after(0, _ui)

        AnkiBackend.export(
            sources=self.sources,
            deck_name=deck,
            output_file=output,
            vault_path=vault,
            flat=flat,
            on_log=self._log,
            on_done=on_done,
        )


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = AnkiUI()
    app.mainloop()
