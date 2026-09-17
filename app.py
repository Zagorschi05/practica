from __future__ import annotations

import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None


GROUP_RE = re.compile(r"^[A-ZĂÂÎȘȚ]{2,4}-?\d{3}$", re.IGNORECASE)
TIME_RE = re.compile(r"^\d{1,2}[.:]\d{2}-\d{1,2}[.:]\d{2}$")
DAY_NAMES = ["Luni", "Marți", "Miercuri", "Joi", "Vineri"]

# The supplied PDF has five horizontal day blocks and seven time rows per block.
TIME_ROWS = ["08:00-09:30", "09:45-11:15", "11:30-13:00",
             "13:30-15:00", "15:15-16:45", "17:00-18:30", "18:45-20:15"]
WEEK_ALL = "Toate"
WEEK_ODD = "Impar\u0103"
WEEK_EVEN = "Par\u0103"
ROOM_ONLY_RE = re.compile(
    r"^(?:(?:Aula|sala|lab\.)\s*)?(?:\d{1,3}(?:-\d{1,3})?|[A-Z]-?\d{1,3})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ScheduleEntry:
    day: str
    interval: str
    group: str
    content: str
    week: str = WEEK_ALL


def _normalise(value: str) -> str:
    value = value.replace("Ș", "S").replace("Ț", "T").replace("ș", "s").replace("ț", "t")
    return re.sub(r"\s+", " ", value).strip()


def _group_columns(words: list[tuple]) -> list[tuple[str, float]]:
    columns = []
    for word in words:
        label = _normalise(word[4])
        if word[1] < 90 and GROUP_RE.fullmatch(label):
            columns.append((label.upper(), (word[0] + word[2]) / 2))
    return columns


def _split_cell_blocks(words: list[tuple], drawings: list[dict]) -> list[str]:
    """Split one timetable cell into vertically stacked weekly alternatives."""
    lines: list[tuple[float, list[str]]] = []
    for word in sorted(words, key=lambda item: (item[1], item[0])):
        centre = (word[1] + word[3]) / 2
        if not lines or abs(centre - lines[-1][0]) > 1.7:
            lines.append((centre, [word[4]]))
        else:
            lines[-1][1].append(word[4])

    separators = [
        drawing["rect"].y0
        for drawing in drawings
        if drawing["rect"].height < 1
        and drawing["rect"].x0 <= min(word[0] for word in words) + 1
        and drawing["rect"].x1 >= max(word[2] for word in words) - 1
        and min(word[1] for word in words) < drawing["rect"].y0 < max(word[3] for word in words)
    ]
    blocks: list[list[str]] = []
    block: list[str] = []
    for centre, values in lines:
        if block and any(previous < separator <= centre for separator in separators for previous in [last_centre]):
            blocks.append(block)
            block = []
        block.append(" ".join(values))
        last_centre = centre
    if block:
        blocks.append(block)
    return [
        _normalise(" | ".join(block))
        for block in blocks
        if _normalise(" | ".join(block))
    ]


def _grid_edges(drawings: list[dict]) -> list[float]:
    """Find the actual vertical table boundaries, not text-label positions."""
    candidates = sorted(
        round(drawing["rect"].x0, 1)
        for drawing in drawings
        if drawing["rect"].width < 1
        and drawing["rect"].height > 20
        and 50 <= drawing["rect"].x0 <= 870
    )
    clusters: list[list[float]] = []
    for value in candidates:
        if not clusters or value - clusters[-1][-1] > 1.5:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    edges = [sum(cluster) / len(cluster) for cluster in clusters]
    if len(edges) < 28:
        raise ValueError("Nu am putut identifica toate coloanele tabelului.")
    return edges[:28]


def _merged_cell_groups(
    block_words: list[tuple],
    row_top: float,
    row_bottom: float,
    edges: list[float],
    vertical_lines: list[tuple[float, float, float]],
) -> list[int]:
    """Return every group covered by a normal or horizontally merged PDF cell."""
    centre_x = sum((word[0] + word[2]) / 2 for word in block_words) / len(block_words)
    separators = {
        round(x, 1)
        for x, line_top, line_bottom in vertical_lines
        if line_top <= row_top + 1 and line_bottom >= row_bottom - 1
    }
    containing = min(
        range(len(edges) - 1),
        key=lambda index: abs(centre_x - (edges[index] + edges[index + 1]) / 2),
    )
    if not separators:
        return [containing]
    start = containing
    end = containing
    while start > 0 and round(edges[start], 1) not in separators:
        start -= 1
    while end < len(edges) - 2 and round(edges[end + 1], 1) not in separators:
        end += 1
    return list(range(start, end + 1))


def _cell_start_index(
    word: tuple,
    row_top: float,
    row_bottom: float,
    edges: list[float],
    vertical_lines: list[tuple[float, float, float]],
) -> int:
    centre_x = (word[0] + word[2]) / 2
    separators = {
        round(x, 1)
        for x, line_top, line_bottom in vertical_lines
        if line_top <= row_top + 1 and line_bottom >= row_bottom - 1
    }
    index = min(
        range(len(edges) - 1),
        key=lambda value: abs(centre_x - (edges[value] + edges[value + 1]) / 2),
    )
    if (
        not separators
        or (
            round(edges[index], 1) not in separators
            and round(edges[index + 1], 1) not in separators
        )
    ):
        return index
    while index > 0 and round(edges[index], 1) not in separators:
        index -= 1
    return index


def _cluster_values(values: list[float], tolerance: float = 1.5) -> list[float]:
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or value - clusters[-1][-1] > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _horizontal_edges(drawings: list[dict]) -> list[float]:
    values = [
        drawing["rect"].y0
        for drawing in drawings
        if drawing["rect"].height < 1
        and drawing["rect"].width > 20
        and 50 <= drawing["rect"].x0 <= 870
        and 50 <= drawing["rect"].y0 <= 805
    ]
    return _cluster_values(values)


def _active_vertical_boundaries(
    edges: list[float],
    vertical_lines: list[tuple[float, float, float]],
    band_top: float,
    band_bottom: float,
) -> list[float]:
    boundaries = {
        round(edges[0], 1),
        round(edges[-1], 1),
    }
    boundaries.update(
        round(x, 1)
        for x, line_top, line_bottom in vertical_lines
        if line_top < band_bottom - 1 and line_bottom > band_top + 1
    )
    return sorted(boundaries)


def _cell_span_for_band(
    left: float,
    right: float,
    row_top: float,
    row_bottom: float,
    edges: list[float],
    vertical_lines: list[tuple[float, float, float]],
) -> tuple[float, float, list[int]]:
    """Return the horizontal span covered by a cell in one vertical band."""
    centre_x = (left + right) / 2
    boundaries = _active_vertical_boundaries(edges, vertical_lines, row_top, row_bottom)
    span_left = boundaries[0]
    span_right = boundaries[-1]
    for boundary_left, boundary_right in zip(boundaries, boundaries[1:]):
        if boundary_left <= centre_x < boundary_right:
            span_left = boundary_left
            span_right = boundary_right
            break
    covered = [
        index for index in range(len(edges) - 1)
        if span_left <= (edges[index] + edges[index + 1]) / 2 < span_right
    ]
    return span_left, span_right, covered


def _cell_text(words: list[tuple]) -> str:
    lines: list[tuple[float, list[str]]] = []
    for word in sorted(words, key=lambda item: (item[1], item[0])):
        centre_y = (word[1] + word[3]) / 2
        if not lines or abs(centre_y - lines[-1][0]) > 1.7:
            lines.append((centre_y, [word[4]]))
        else:
            lines[-1][1].append(word[4])
    return " | ".join(
        _normalise(" ".join(values))
        for _, values in lines
    )


def _merge_room_fragments(entries: list[ScheduleEntry]) -> list[ScheduleEntry]:
    """Join room-only squares back to the activity immediately before them."""
    result: list[ScheduleEntry] = []
    last_by_slot: dict[tuple[str, str, str], int] = {}
    for entry in entries:
        slot = (entry.day, entry.interval, entry.group)
        previous_index = last_by_slot.get(slot)
        if (
            previous_index is not None
            and ROOM_ONLY_RE.fullmatch(entry.content)
            and not ROOM_ONLY_RE.fullmatch(result[previous_index].content)
        ):
            previous = result[previous_index]
            result[previous_index] = ScheduleEntry(
                previous.day,
                previous.interval,
                previous.group,
                f"{previous.content} | {entry.content}",
                previous.week,
            )
        else:
            last_by_slot[slot] = len(result)
            result.append(entry)
    return result


def parse_pdf(path: str | Path) -> list[ScheduleEntry]:
    """Read the timetable by coordinates, preserving the PDF's visual grid."""
    if pymupdf is None:
        raise RuntimeError("Lipsește PyMuPDF. Rulează: pip install pymupdf")

    document = pymupdf.open(str(path))
    try:
        if not document.page_count:
            raise ValueError("PDF-ul nu conține pagini.")
        page = document[0]
        words = page.get_text("words")
        drawings = page.get_drawings()
    finally:
        document.close()

    columns = _group_columns(words)
    if not columns:
        raise ValueError("Nu am găsit anteturile grupelor în PDF.")
    columns.sort(key=lambda item: item[1])

    # The time labels are printed at x=41 and x=869. Their y coordinates
    # identify the seven rows in each of the five day blocks.
    time_words = [
        word for word in words
        if word[0] < 60 and TIME_RE.fullmatch(_normalise(word[4]))
    ]
    row_centres = sorted({round((word[1] + word[3]) / 2, 1) for word in time_words})
    if len(row_centres) < 35:
        raise ValueError("Nu am putut identifica rândurile de timp din PDF.")
    row_centres = row_centres[:35]

    edges = _grid_edges(drawings)
    horizontal_edges = _horizontal_edges(drawings)
    vertical_lines = [
        (round(drawing["rect"].x0, 1), drawing["rect"].y0, drawing["rect"].y1)
        for drawing in drawings
        if drawing["rect"].width < 1
        and drawing["rect"].height > 3
        and drawing["rect"].x0 >= edges[0] - 2
        and drawing["rect"].x0 <= edges[-1] + 2
    ]

    entries = []
    table_top = horizontal_edges[0]
    table_bottom = horizontal_edges[-1]
    for row_index, row_centre in enumerate(row_centres):
        row_top = (
            (row_centres[row_index - 1] + row_centre) / 2
            if row_index > 0 else table_top
        )
        row_bottom = (
            (row_centre + row_centres[row_index + 1]) / 2
            if row_index + 1 < len(row_centres) else table_bottom
        )
        cell_words = [
            word for word in words
            if 90 <= word[1] < 805
            and row_top <= (word[1] + word[3]) / 2 <= row_bottom
            and edges[0] <= (word[0] + word[2]) / 2 < edges[-1]
        ]
        if not cell_words:
            continue

        active = _active_vertical_boundaries(edges, vertical_lines, row_top, row_bottom)
        processed_cells: set[tuple[str, float, float, float, float]] = set()
        for left, right in zip(active, active[1:]):
            words_in_cell = [
                word for word in cell_words
                if left <= (word[0] + word[2]) / 2 < right
            ]
            if not words_in_cell:
                continue
            day = DAY_NAMES[row_index // 7]
            interval = TIME_ROWS[row_index % 7]
            split_candidates = [
                drawing["rect"].y0
                for drawing in drawings
                if drawing["rect"].height < 1
                and drawing["rect"].width > 5
                and row_top < drawing["rect"].y0 < row_bottom
                and drawing["rect"].x0 <= left + 1
                and drawing["rect"].x1 >= right - 1
                and abs(drawing["rect"].y0 - row_centre) <= (row_bottom - row_top) * 0.35
            ]
            split_y = (
                min(split_candidates, key=lambda value: abs(value - row_centre))
                if split_candidates else None
            )
            blocks: list[tuple[list[tuple], str]]
            if split_y is not None:
                band_specs = [
                    (row_top, split_y, WEEK_ODD),
                    (split_y, row_bottom, WEEK_EVEN),
                ]
            else:
                band_specs = [(row_top, row_bottom, WEEK_ALL)]

            for band_top, band_bottom, week in band_specs:
                span_left, span_right, covered = _cell_span_for_band(
                    left,
                    right,
                    band_top,
                    band_bottom,
                    edges,
                    vertical_lines,
                )
                cell_key = (
                    week,
                    round(span_left, 1),
                    round(span_right, 1),
                    round(band_top, 1),
                    round(band_bottom, 1),
                )
                if cell_key in processed_cells:
                    continue
                processed_cells.add(cell_key)
                block_words = [
                    word for word in cell_words
                    if span_left <= (word[0] + word[2]) / 2 < span_right
                    and band_top <= (word[1] + word[3]) / 2 < band_bottom
                ]
                if not block_words:
                    continue
                content = _cell_text(block_words)
                if not content:
                    continue
                for group_index in covered:
                    entries.append(
                        ScheduleEntry(day, interval, columns[group_index][0], content, week)
                    )
    return _merge_room_fragments(entries)


class ScheduleApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Orar pe grupe")
        self.geometry("1150x700")
        self.minsize(850, 500)
        self.entries: list[ScheduleEntry] = []
        self._build_ui()

    def _build_ui(self) -> None:
        self.configure(bg="#f4f7fb")
        self.option_add("*Font", ("Segoe UI", 10))
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        self.option_add("*TCombobox*Listbox.background", "#ffffff")
        self.option_add("*TCombobox*Listbox.foreground", "#344054")
        self.option_add("*TCombobox*Listbox.selectBackground", "#dbe7ff")
        self.option_add("*TCombobox*Listbox.selectForeground", "#172033")
        self.option_add("*TCombobox*Listbox.borderWidth", 0)
        self.option_add("*TCombobox*Listbox.relief", "flat")

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#f4f7fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure(
            "Title.TLabel",
            background="#ffffff",
            foreground="#172033",
            font=("Segoe UI", 22, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background="#ffffff",
            foreground="#667085",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Field.TLabel",
            background="#ffffff",
            foreground="#344054",
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background="#ffffff",
            foreground="#667085",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Status.TLabel",
            background="#f4f7fb",
            foreground="#667085",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Accent.TButton",
            background="#3867d6",
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            padding=(16, 9),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#2f56b8"), ("pressed", "#25479a")],
            foreground=[("disabled", "#d0d5dd")],
        )
        style.configure(
            "Modern.TCombobox",
            fieldbackground="#ffffff",
            background="#ffffff",
            foreground="#172033",
            bordercolor="#cbd5e1",
            lightcolor="#cbd5e1",
            darkcolor="#cbd5e1",
            padding=(10, 7),
            arrowsize=16,
        )
        style.map(
            "Modern.TCombobox",
            fieldbackground=[
                ("readonly", "#ffffff"),
                ("focus", "#f8fbff"),
            ],
            bordercolor=[("focus", "#3867d6")],
            lightcolor=[("focus", "#3867d6")],
            darkcolor=[("focus", "#3867d6")],
            selectbackground=[("readonly", "#ffffff")],
            selectforeground=[("readonly", "#172033")],
        )
        style.configure(
            "Modern.Treeview",
            background="#ffffff",
            fieldbackground="#ffffff",
            foreground="#344054",
            rowheight=44,
            borderwidth=0,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Modern.Treeview.Heading",
            background="#3867d6",
            foreground="#ffffff",
            relief="flat",
            borderwidth=0,
            padding=(12, 11),
            font=("Segoe UI", 9, "bold"),
        )
        style.configure(
            "Day.Treeview",
            background="#e8efff",
            foreground="#25479a",
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Modern.Treeview",
            background=[("selected", "#dbe7ff")],
            foreground=[("selected", "#172033")],
            fieldbackground=[("selected", "#dbe7ff")],
        )
        style.configure(
            "Modern.Vertical.TScrollbar",
            background="#94a3b8",
            troughcolor="#e8edf5",
            bordercolor="#e8edf5",
            lightcolor="#e8edf5",
            darkcolor="#e8edf5",
            borderwidth=0,
            arrowsize=14,
            width=12,
        )
        style.map(
            "Modern.Vertical.TScrollbar",
            background=[("active", "#64748b"), ("pressed", "#475569")],
        )

        header = ttk.Frame(self, style="App.TFrame", padding=(28, 24, 28, 16))
        header.pack(fill="x")
        card = ttk.Frame(header, style="Card.TFrame", padding=(24, 22, 24, 20))
        card.pack(fill="x")
        ttk.Label(card, text="Orar pe grupe", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            card,
            text="Deschide PDF-ul real și afișează activitățile organizate automat pe grupe.",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(4, 18))

        controls = ttk.Frame(card, style="Card.TFrame")
        controls.pack(fill="x")
        ttk.Button(
            controls, text="Deschide PDF", command=self.open_pdf, style="Accent.TButton",
        ).pack(side="left")
        self.file_label = ttk.Label(
            controls, text="Niciun fișier selectat", style="Muted.TLabel",
        )
        self.file_label.pack(side="left", padx=(14, 20))

        ttk.Label(controls, text="GRUPĂ", style="Field.TLabel").pack(side="left", padx=(0, 6))
        self.group_var = tk.StringVar(value="Toate")
        self.group_box = ttk.Combobox(
            controls, textvariable=self.group_var, state="readonly",
            width=14, style="Modern.TCombobox", postcommand=self._style_dropdowns,
        )
        self.group_box["values"] = ("Toate",)
        self.group_box.pack(side="left")
        self.group_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="ZI", style="Field.TLabel").pack(side="left", padx=(18, 6))
        self.day_var = tk.StringVar(value="Toate")
        self.day_box = ttk.Combobox(
            controls, textvariable=self.day_var, state="readonly",
            width=12, style="Modern.TCombobox", postcommand=self._style_dropdowns,
        )
        self.day_box["values"] = ("Toate", *DAY_NAMES)
        self.day_box.pack(side="left")
        self.day_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="SĂPTĂMÂNĂ", style="Field.TLabel").pack(
            side="left", padx=(18, 6),
        )
        self.week_var = tk.StringVar(value=WEEK_ALL)
        self.week_box = ttk.Combobox(
            controls, textvariable=self.week_var, state="readonly",
            width=10, style="Modern.TCombobox", postcommand=self._style_dropdowns,
        )
        self.week_box["values"] = (WEEK_ALL, WEEK_ODD, WEEK_EVEN)
        self.week_box.pack(side="left")
        self.week_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        table_frame = ttk.Frame(self, style="App.TFrame", padding=(28, 0, 28, 12))
        table_frame.pack(fill="both", expand=True)
        columns = ("day", "time", "group", "week", "content")
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", style="Modern.Treeview",
        )
        self.table.tag_configure("even", background="#ffffff")
        self.table.tag_configure("odd", background="#f8fafc")
        self.table.tag_configure("day", background="#e8efff", foreground="#25479a")
        headings = {
            "day": "Zi / ziua săptămânii",
            "time": "Interval",
            "group": "Grupă",
            "week": "Săptămână",
            "content": "Activitate, sală și profesor",
        }
        widths = {"day": 165, "time": 125, "group": 110, "week": 105, "content": 550}
        for name in columns:
            self.table.heading(name, text=headings[name])
            self.table.column(name, width=widths[name], anchor="w")
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview,
            style="Modern.Vertical.TScrollbar",
        )
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.status = ttk.Label(
            self, text="Selectează un PDF pentru a vedea orarul.",
            style="Status.TLabel", padding=(28, 0, 28, 18),
        )
        self.status.pack(anchor="w")

    def _style_dropdowns(self) -> None:
        """Keep the native combobox popup consistent with the application theme."""
        for combo in (self.group_box, self.day_box, self.week_box):
            popup = self.tk.call("ttk::combobox::PopdownWindow", str(combo))
            listbox = f"{popup}.f.l"
            try:
                self.tk.call(
                    listbox, "configure",
                    "-background", "#ffffff",
                    "-foreground", "#344054",
                    "-selectbackground", "#dbe7ff",
                    "-selectforeground", "#172033",
                    "-font", "Segoe UI 10",
                    "-borderwidth", 0,
                    "-highlightthickness", 0,
                    "-relief", "flat",
                )
            except tk.TclError:
                continue

    def open_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Selectează orarul PDF",
            filetypes=(("Fișiere PDF", "*.pdf"), ("Toate fișierele", "*.*")),
        )
        if not path:
            return
        try:
            self.entries = parse_pdf(path)
        except (RuntimeError, ValueError, OSError) as error:
            messagebox.showerror("Eroare la citirea PDF-ului", str(error))
            return
        self.file_label.configure(text=Path(path).name)
        groups = sorted({entry.group for entry in self.entries})
        self.group_box["values"] = ("Toate", *groups)
        self.group_var.set("Toate")
        self.day_var.set("Toate")
        self.week_var.set("Toate")
        self.refresh()

    def refresh(self) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        group = self.group_var.get()
        day = self.day_var.get()
        week = self.week_var.get()
        filtered = [
            entry for entry in self.entries
            if (group == "Toate" or entry.group == group)
            and (day == "Toate" or entry.day == day)
            and (week == "Toate" or entry.week in (week, "Toate"))
        ]
        day_order = {name: index for index, name in enumerate(DAY_NAMES)}
        time_order = {value: index for index, value in enumerate(TIME_ROWS)}
        filtered.sort(key=lambda entry: (
            day_order[entry.day], time_order.get(entry.interval, 0), entry.group,
        ))

        grouped: dict[tuple[str, str, str], list[ScheduleEntry]] = {}
        for entry in filtered:
            key = (entry.day, entry.interval, entry.group)
            grouped.setdefault(key, [])
            if (entry.week, entry.content) not in {
                (item.week, item.content) for item in grouped[key]
            }:
                grouped[key].append(entry)

        entries_by_day: dict[str, list[tuple[tuple[str, str, str], list[ScheduleEntry]]]] = {}
        for key, alternatives in grouped.items():
            entries_by_day.setdefault(key[0], []).append((key, alternatives))

        row_index = 0
        for day in DAY_NAMES:
            day_entries = entries_by_day.get(day)
            if not day_entries:
                continue
            self.table.insert("", "end", values=(day, "", "", "", ""), tags=("day",))
            for (entry_day, entry_interval, entry_group), alternatives in day_entries:
                alternatives.sort(key=lambda item: (item.week, item.content))
                content = "  •  ".join(
                    f"{item.week}: {item.content}" if item.week != WEEK_ALL else item.content
                    for item in alternatives
                )
                self.table.insert(
                    "", "end",
                    values=(
                        "",
                        entry_interval,
                        entry_group,
                        " / ".join(sorted({item.week for item in alternatives})),
                        content,
                    ),
                    tags=("even" if row_index % 2 == 0 else "odd",),
                )
                row_index += 1
        self.status.configure(text="Orarul este gata.")


if __name__ == "__main__":
    ScheduleApp().mainloop()
