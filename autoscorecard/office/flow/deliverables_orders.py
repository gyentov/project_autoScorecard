"""Orders the office can do itself: documentation, deck, feature meanings."""
from __future__ import annotations

import re

from .. import deliverables as DELIV


class DeliverableOrdersMixin:
    # (action, owner, what in the Boss's words asks for it); the first match wins
    CAPABILITIES = [
        ("make_documentation", "laras", re.compile(
            r"dokumentasi\w*\s+(?:\w+\s+){0,2}(model|proyek|scorecard)|(model|proyek)\w*\s+.*dokumentasi", re.I)),
        ("make_deck", "kirana", re.compile(r"\b(deck|slide|presentasi|ppt|pptx)\b", re.I)),
        ("feature_meanings", "citra", re.compile(
            r"(pasang|cocok|map)\w*\W+(?:\w+\W+){0,4}?fitur|fitur\s+(terpilih|final|model)\W+(?:\w+\W+){0,3}?"
            r"(arti|makna|kamus)|(arti|makna|kamus)\W+(?:\w+\W+){0,3}?fitur\s+(terpilih|final|model)", re.I)),
        ("document_variables", "bening", re.compile(
            r"(infer\w*|arti|makna|terjemah\w*|kamus|dokumentasi\w*|jelaskan)\W+(?:\w+\W+){0,4}?"
            r"(variabel|kolom|tabel|kamus|data)|(variabel|kolom|tabel)\W+(?:\w+\W+){0,4}?"
            r"(infer\w*|arti|makna|terjemah\w*)", re.I)),
    ]

    def _capability(self, text: str) -> tuple[str, str] | None:
        return next(((a, o) for a, o, rx in self.CAPABILITIES if rx.search(text)), None)

    def run_order(self, order: dict) -> str:
        """Do an order the office knows how to do. Returns what was done; raises
        ValueError (with the reason) when it cannot be done yet."""
        action = order.get("action")
        if action == "document_variables":
            r = self.document_variables(translate=True)
            return (f"Kamus variabel dan tabel sudah saya dokumentasikan ({len(r['rows'])} variabel, "
                    f"{len(r['tables'])} tabel" + (f", {r['translated']} deskripsi diterjemahkan" if r["translated"] else "")
                    + "). Filenya terlampir di email.")
        if action == "make_documentation":
            r = self.make_documentation()
            return f"Dokumentasi model ({r['cut'].replace('cut', 'Cut ')}) sudah jadi, terlampir di email."
        if action == "feature_meanings":
            r = self.feature_meanings()
            return (f"Kamus sudah saya pasangkan ke {r['n']} fitur di model {r['cut'].replace('cut', 'Cut ')} "
                    "(arti, poin, kontribusi); terlampir di email.")
        if action == "make_deck":
            r = self.make_deck()
            return f"Draft deck ({r['slides']} slide, HTML) sudah jadi untuk Boss review; terlampir di email."
        raise ValueError("belum ada cara otomatis; saya kerjakan dan laporkan")

    def run_open_orders(self) -> None:
        """Retry the orders that were blocked, now that the project moved on."""
        for o in self.orders.open_for():
            if not o.get("action"):
                continue
            try:
                done = self.run_order(o)
            except ValueError:
                continue
            self.orders.update(o["id"], "selesai", done)
            self.events.emit("dialogue", o["to"], mood="happy",
                             text=f"Perintah #{o['id']} selesai, Boss: {done}")

    def make_documentation(self) -> dict:
        r = DELIV.documentation(self)
        self.inbox.send("laras", "Dokumentasi model", f"Boss,\n\nDokumentasi model untuk "
                        f"{r['cut'].replace('cut', 'Cut ')} sudah saya susun: "
                        + ", ".join(r["sections"]) + ".\n\nLaras", "results", attachments=[r["file"]])
        return r

    def feature_meanings(self) -> dict:
        r = DELIV.selected_features(self)
        self.inbox.send("citra", "Arti fitur terpilih", f"Boss,\n\nKamus Bening sudah saya pasangkan "
                        f"ke {r['n']} fitur di model {r['cut'].replace('cut', 'Cut ')}: arti, rentang poin, "
                        "dan kontribusi skor masing-masing.\n\nCitra", "results", attachments=[r["file"]])
        return r

    def make_deck(self) -> dict:
        r = DELIV.deck(self)
        self.inbox.send("kirana", "Draft deck untuk review", f"Boss,\n\nDraft deck "
                        f"({r['slides']} slide, bahasa Inggris, HTML) dari {r['cut'].replace('cut', 'Cut ')} "
                        "sudah siap. Kalau Boss setuju, saya ekspor ke PDF / PPTX.\n\nKirana",
                        "results", attachments=[r["file"]])
        self.timeline.set("deck", "waiting_boss", "draft HTML menunggu review Boss")
        return r
