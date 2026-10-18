"""The board: where the team SHOWS the Boss data (aggregates, charts, sample rows),
instead of writing tables into the chat.

In a meeting a board item becomes a new slide on the projector. At a desk it opens
on the board window in the office. Either way it is kept:
projects/<id>/work/board/<n>.html, listed in work/board/index.json.

The exploration functions read the real data. Aggregates may go back to the model
(they are what the agents are allowed to see). Sample rows go to the board ONLY:
the model is told how many rows and which columns it showed, never the values.
"""
from __future__ import annotations

import difflib
import html
import json
from datetime import datetime, timezone

import pandas as pd

from . import prep
from .meetings import _page, html_table

def _no_column(c: str, df: pd.DataFrame) -> KeyError:
    """A missing column, with the closest real names (e.g. cust_province -> province)."""
    cols = [str(x) for x in df.columns]
    near = [x for x in cols if x in c or c in x] + difflib.get_close_matches(c, cols, 3, 0.5)
    near = list(dict.fromkeys(near))[:3]
    return KeyError(f"kolom '{c}' tidak ada" + (f"; mungkin maksudnya: {', '.join(near)}"
                                                if near else ""))


METRICS = ("count", "mean", "sum", "min", "max", "median", "nunique", "missing_rate",
           "bad_rate")
OPS = {"==": "eq", "!=": "ne", ">": "gt", ">=": "ge", "<": "lt", "<=": "le"}
MAX_GROUPS, MAX_SAMPLE = 25, 20


class Board:
    def __init__(self, project, events, meetings):
        self.project, self.events, self.meetings = project, events, meetings
        self.dir = project.dir("work") / "board"

    def _index(self) -> list[dict]:
        p = self.dir / "index.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def list(self) -> list[dict]:
        return self._index()

    def show(self, title: str, body_html: str, by: str, meeting_id: str | None = None) -> dict:
        """Put an item on the board; in a meeting it goes on the projector's board tab,
        apart from the prepared deck."""
        self.dir.mkdir(parents=True, exist_ok=True)
        items = self._index()
        n = len(items) + 1
        f = self.dir / f"{n}.html"
        f.write_text(_page(title, body_html), encoding="utf-8")
        item = {"n": n, "title": title, "file": str(f.relative_to(self.project.root)), "by": by,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "meeting_id": meeting_id, "board": None}
        if meeting_id:
            m = self.meetings.get(meeting_id)
            m.board.append({"title": title, "file": item["file"], "by": by})
            self.meetings.save(m)
            item["board"] = len(m.board) - 1
        items.append(item)
        (self.dir / "index.json").write_text(json.dumps(items, indent=1), encoding="utf-8")
        self.events.emit("board", by, title=title, file=item["file"], meeting_id=meeting_id,
                         board=item["board"])
        return item


# --------------------------------------------------------------------------- data access
def frame(office, table: str | None = None) -> tuple[pd.DataFrame, str]:
    """(data, what it is): an uploaded table by name, or the joined dataset with the
    `bad` flag once the target is defined (the default once the join exists)."""
    tables, _ = prep.split_uploads(office.project)
    if table and table not in ("dataset", "gabungan"):
        key = table.rsplit(".", 1)[0]
        if key not in tables:
            raise KeyError(f"tabel '{table}' tidak ada; tersedia: dataset, {', '.join(tables)}")
        return tables[key], f"tabel {key}"
    try:
        df, y = office.dataset()
    except FileNotFoundError:
        if not tables:
            raise ValueError("belum ada data yang diserahkan ke Laras")
        if table in ("dataset", "gabungan"):
            raise ValueError("dataset gabungan belum ada (join belum selesai)")
        # before the join: the main table (applications / loans ...), else the largest
        name = max(tables, key=lambda n: (bool(prep.BASE_HINT.search(n)), len(tables[n])))
        return tables[name], f"tabel {name}"
    df = df.copy()
    if y is not None:
        df["bad"] = y.reindex(df.index).astype("Int64")
    return df, "dataset gabungan"


def _filter(df: pd.DataFrame, filters: list[dict] | None) -> tuple[pd.DataFrame, str]:
    notes = []
    for f in filters or []:
        c, op, v = f["column"], f.get("op", "=="), f.get("value")
        if c not in df.columns:
            raise _no_column(c, df)
        if op == "in":
            df = df[df[c].isin(v if isinstance(v, list) else [v])]
        elif op in OPS:
            df = df[getattr(df[c], OPS[op])(v)]
        else:
            raise ValueError(f"operator {op} tidak dikenal")
        notes.append(f"{c} {op} {v}")
    return df, "; ".join(notes)


def num(v) -> str:
    """Readable numbers: 47,500,000 not 4.75e+07; 21 not 20.999."""
    v = float(v)
    return f"{v:,.0f}" if abs(v) >= 1e4 else f"{v:.4g}"


def _groups(s: pd.Series, bins: int) -> pd.Series:
    """Numeric columns with many values become quantile bands ("21 – 30", in numeric
    order); categories keep their biggest values and fold the rest into 'lainnya'."""
    if pd.api.types.is_numeric_dtype(s) and s.nunique() > 12:
        cut = pd.qcut(s, q=bins, duplicates="drop")
        cut = cut.cat.rename_categories([f"{num(i.left)} – {num(i.right)}"
                                         for i in cut.cat.categories])
        if cut.isna().any():
            cut = cut.cat.add_categories("(kosong)").fillna("(kosong)")
        return cut
    s = s.astype("object").where(s.notna(), "(kosong)").astype(str)
    top = s.value_counts().index[:MAX_GROUPS - 1]
    return s.where(s.isin(top), "lainnya")


def aggregate(df: pd.DataFrame, group_by: list[str], metrics: list[dict],
              bins: int = 5) -> pd.DataFrame:
    for c in group_by:
        if c not in df.columns:
            raise _no_column(c, df)
    metrics = metrics or [{"agg": "count"}]
    keys = [_groups(df[c], bins).rename(c) for c in group_by]
    g = df.groupby(keys, observed=True, sort=True) if keys else None
    out = {}
    for mt in metrics:
        agg, col = mt.get("agg", "count"), mt.get("column")
        if agg not in METRICS:
            raise ValueError(f"agregasi '{agg}' tidak dikenal; pilih {', '.join(METRICS)}")
        if agg == "bad_rate":
            if "bad" not in df.columns:
                raise ValueError("target belum didefinisikan, jadi bad rate belum ada")
            col = "bad"
        if agg != "count" and col is None:
            raise KeyError(f"agregasi {agg} butuh nama kolom")
        if agg != "count" and col not in df.columns:
            raise _no_column(col, df)
        name = "n" if agg == "count" else f"{agg}({col})"
        src = df[col] if col else df.iloc[:, 0]
        if agg == "count":
            vals = g.size() if g is not None else pd.Series([len(df)])
        elif agg == "missing_rate":
            vals = (src.isna().groupby(keys, observed=True).mean() if g is not None
                    else pd.Series([src.isna().mean()]))
        else:
            fn = "mean" if agg == "bad_rate" else agg
            s = pd.to_numeric(src, errors="coerce") if fn not in ("nunique", "min", "max") else src
            vals = (s.groupby(keys, observed=True).agg(fn) if g is not None
                    else pd.Series([s.agg(fn)]))
        out[name] = vals
    t = pd.DataFrame(out).reset_index() if keys else pd.DataFrame(out)
    return t.head(MAX_GROUPS)


def bars_svg(t: pd.DataFrame, label_cols: list[str], value_col: str, pct: bool) -> str:
    """A small horizontal bar chart: one hue, thin bars, value labels in text ink."""
    rows = t.head(MAX_GROUPS)
    vals = pd.to_numeric(rows[value_col], errors="coerce").fillna(0).tolist()
    top = max(vals) or 1
    h, w, lw = 22, 640, 180
    lines = []
    for k, (_, r) in enumerate(rows.iterrows()):
        label = " · ".join(str(r[c]) for c in label_cols) or "semua"
        v = vals[k]
        bw = max(2, (w - lw - 90) * v / top)
        y = k * h + 4
        txt = f"{v:.1%}" if pct else (f"{v:,.0f}" if float(v).is_integer() else f"{v:,.3g}")
        lines.append(f'<text x="{lw - 8}" y="{y + 14}" text-anchor="end" font-size="12" '
                     f'fill="var(--t2,#52514e)">{html.escape(label[:28])}</text>'
                     f'<rect x="{lw}" y="{y + 3}" width="{bw:.1f}" height="14" rx="3" fill="#2a78d6"/>'
                     f'<text x="{lw + bw + 6:.1f}" y="{y + 14}" font-size="12" '
                     f'fill="var(--t1,#0b0b0b)">{txt}</text>')
    return (f'<svg viewBox="0 0 {w} {len(rows) * h + 8}" width="100%" role="img" '
            f'aria-label="{html.escape(value_col)}" style="max-width:{w}px">{"".join(lines)}</svg>')


def records(t: pd.DataFrame) -> list[dict]:
    t = t.copy()
    for c in t.columns:
        if pd.api.types.is_float_dtype(t[c]):
            t[c] = t[c].round(4)
        elif pd.api.types.is_datetime64_any_dtype(t[c]):
            t[c] = t[c].dt.strftime("%Y-%m-%d")
        elif isinstance(t[c].dtype, pd.CategoricalDtype):
            t[c] = t[c].astype(str)
    return json.loads(t.to_json(orient="records", date_format="iso", default_handler=str))


def sample(df: pd.DataFrame, n: int = 10, columns: list[str] | None = None,
           random: bool = True) -> pd.DataFrame:
    n = max(1, min(int(n or 10), MAX_SAMPLE))
    if columns:
        missing = [c for c in columns if c not in df.columns]
        if missing:
            raise _no_column(missing[0], df)
        df = df[columns]
    elif df.shape[1] > 10:                                  # a readable slide: first 10 columns
        df = df.iloc[:, :10]
    return df.sample(min(n, len(df)), random_state=7) if random else df.head(n)


def profile_table(df: pd.DataFrame) -> pd.DataFrame:
    rows = [{"kolom": c, "tipe": str(df[c].dtype), "missing": float(df[c].isna().mean()),
             "unik": int(df[c].nunique())} for c in df.columns]
    return pd.DataFrame(rows)


def markdown_tables(text: str) -> tuple[list[str], str]:
    """Markdown tables written into a reply: (the tables, the text without them)."""
    tables, cur, keep = [], [], []
    for ln in text.splitlines():
        if ln.strip().startswith("|") and ln.strip().endswith("|"):
            cur.append(ln)
            continue
        if len(cur) >= 2:
            tables.append("\n".join(cur))
        elif cur:
            keep.extend(cur)
        cur = []
        keep.append(ln)
    if len(cur) >= 2:
        tables.append("\n".join(cur))
    elif cur:
        keep.extend(cur)
    return tables, "\n".join(keep).strip()


def markdown_to_html(md: str) -> str:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in md.splitlines()
            if not set(ln.replace("|", "").strip()) <= set("-: ")]
    if not rows:
        return ""
    head, body = rows[0], rows[1:]
    return html_table([dict(zip(head, r)) for r in body], head)
