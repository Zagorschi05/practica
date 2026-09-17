from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, W, X, Y

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageFont = None


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
TEACHER_RE = re.compile(
    r"^[A-ZĂÂÎȘȚ][A-Za-zĂÂÎȘȚăâîșț\-']+(?:\s+[A-ZĂÂÎȘȚ]\.)+$",
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


COLOR_COURSE = (157, 195, 230)  # blue
COLOR_SEMINAR = (169, 208, 142)  # green
COLOR_LAB = (244, 177, 131)  # orange/red
COLOR_EMPTY = (217, 217, 217)  # grey
COLOR_HEADER = (255, 255, 255)
COLOR_BORDER = (0, 0, 0)
COLOR_TEXT = (0, 0, 0)

DAY_HEADERS = ["LUNI", "MARȚI", "MIERCURI", "JOI", "VINERI"]
LIGHT_THEME = "cosmo"
DARK_THEME = "darkly"


@dataclass(frozen=True)
class ParsedActivity:
    subject: str
    teacher: str
    room: str
    kind: str  # course | seminar | lab | empty


def _activity_kind(subject: str) -> str:
    lowered = subject.lower().strip()
    if lowered.startswith("c.") or lowered.startswith("curs"):
        return "course"
    if lowered.startswith("lab"):
        return "lab"
    return "seminar"


def _shorten_room(room: str) -> str:
    room = room.strip()
    if not room:
        return ""
    # Keep the useful room token, drop long venue suffixes.
    if room.lower().startswith("aula"):
        parts = room.split()
        return " ".join(parts[:2]) if len(parts) >= 2 else room
    tokens = room.split()
    if len(tokens) >= 2 and re.fullmatch(r"[\dA-Za-z-]+", tokens[0]):
        return tokens[0]
    return room


def _looks_like_teacher(text: str) -> bool:
    return bool(TEACHER_RE.fullmatch(text.strip()))


def _looks_like_room(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if ROOM_ONLY_RE.fullmatch(text):
        return True
    if re.match(r"^(?:Aula|sala)\b", text, re.IGNORECASE):
        return True
    # e.g. "3-3 Amdaris", "115 / 630"
    if re.match(r"^\d{1,3}(?:-\d{1,3})?(?:\s|/)", text):
        return True
    return False


def _join_subject_parts(parts: list[str]) -> str:
    subject = " ".join(part.strip() for part in parts if part.strip())
    subject = re.sub(r"\s*/\s*", "/", subject)
    subject = re.sub(r"\(\s+", "(", subject)
    subject = re.sub(r"\s+\)", ")", subject)
    subject = re.sub(r"\s+", " ", subject).strip()
    # Prefer a readable space after slash inside month ranges.
    subject = re.sub(
        r"/(?=(?:ianuarie|februarie|martie|aprilie|mai|iunie|iulie|august|"
        r"septembrie|octombrie|octombire|noiembrie|decembrie))",
        "/ ",
        subject,
        flags=re.IGNORECASE,
    )
    return subject


def _split_activity_parts(content: str) -> tuple[str, str, str]:
    """Map visual PDF lines to subject / teacher / room without breaking normal cells."""
    parts = [part.strip() for part in content.split("|") if part.strip()]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""

    room = ""
    teachers: list[str] = []
    index = len(parts) - 1
    if index >= 0 and _looks_like_room(parts[index]):
        room = parts[index]
        index -= 1
    while index >= 0 and _looks_like_teacher(parts[index]):
        teachers.insert(0, parts[index])
        index -= 1
    subject_parts = parts[: index + 1]

    # Classic 3-line cell already split correctly.
    if len(parts) == 3 and teachers and room and len(subject_parts) == 1:
        return subject_parts[0], teachers[0], room

    # Wrapped subject lines: keep merging leftover non-teacher/non-room fragments.
    if not teachers and len(parts) >= 3:
        # Fallback for unusual teacher formats: last non-room token is teacher.
        if room and index >= 0:
            teachers = [parts[index]]
            subject_parts = parts[:index]
        elif not room and index >= 0:
            teachers = [parts[index]]
            subject_parts = parts[:index]

    subject = _join_subject_parts(subject_parts) if subject_parts else parts[0]
    teacher = " / ".join(teachers)
    return subject, teacher, room


def parse_activity(content: str) -> ParsedActivity:
    """Split cell text into subject, teacher and room, repairing wrapped subject lines."""
    subject, teacher, room = _split_activity_parts(content)
    room = _shorten_room(room)
    if not subject or subject == "-":
        return ParsedActivity("-", "", "", "empty")
    return ParsedActivity(subject, teacher, room, _activity_kind(subject))


def _color_for_kind(kind: str) -> tuple[int, int, int]:
    if kind == "course":
        return COLOR_COURSE
    if kind == "lab":
        return COLOR_LAB
    if kind == "empty":
        return COLOR_EMPTY
    return COLOR_SEMINAR


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _display_interval(interval: str) -> str:
    return interval.replace(":", ".")


def _activity_lines(activity: ParsedActivity) -> list[str]:
    if activity.kind == "empty":
        return ["-"]
    lines = [activity.subject]
    if activity.teacher:
        lines.append(activity.teacher)
    if activity.room:
        lines.append(activity.room)
    return lines


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    trimmed = text
    while trimmed and draw.textlength(trimmed + ellipsis, font=font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


def _wrap_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    if draw.textlength(text, font=font) <= max_width:
        return [text]
    words = text.split()
    if len(words) <= 1:
        return [_fit_text(draw, text, font, max_width)]
    lines: list[str] = []
    index = 0
    while index < len(words) and len(lines) < max_lines:
        current = words[index]
        index += 1
        while index < len(words):
            candidate = f"{current} {words[index]}"
            if draw.textlength(candidate, font=font) <= max_width:
                current = candidate
                index += 1
            else:
                break
        if len(lines) == max_lines - 1 and index < len(words):
            leftover = " ".join([current, *words[index:]])
            lines.append(_fit_text(draw, leftover, font, max_width))
            break
        lines.append(current)
    return lines


def _draw_wrapped_block(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    lines: list[str],
    fill: tuple[int, int, int],
    font: ImageFont.ImageFont,
    bold_font: ImageFont.ImageFont,
) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, fill=fill, outline=COLOR_BORDER, width=2)
    usable_width = right - left - 12
    usable_height = bottom - top - 8
    rendered: list[tuple[str, ImageFont.ImageFont]] = []
    for index, line in enumerate(lines):
        use_font = bold_font if index == 0 else font
        wrapped = _wrap_line(draw, line, use_font, usable_width, max_lines=2 if index == 0 else 1)
        for part in wrapped:
            rendered.append((part, use_font))
    line_height = max(font.size, bold_font.size) + 2
    total_height = line_height * len(rendered)
    while total_height > usable_height and len(rendered) > 1:
        rendered.pop()
        total_height = line_height * len(rendered)
    y = top + max(4, (usable_height - total_height) // 2)
    for text, use_font in rendered:
        width = draw.textlength(text, font=use_font)
        x = left + (right - left - width) / 2
        draw.text((x, y), text, fill=COLOR_TEXT, font=use_font)
        y += line_height


def _slot_entries(
    entries: list[ScheduleEntry],
    group: str,
    week_filter: str = WEEK_ALL,
) -> dict[tuple[str, str], list[ScheduleEntry]]:
    slots: dict[tuple[str, str], list[ScheduleEntry]] = {}
    for entry in entries:
        if entry.group != group:
            continue
        if week_filter != WEEK_ALL and entry.week not in (week_filter, WEEK_ALL):
            continue
        key = (entry.day, entry.interval)
        bucket = slots.setdefault(key, [])
        if (entry.week, entry.content) not in {(item.week, item.content) for item in bucket}:
            bucket.append(entry)
    for bucket in slots.values():
        bucket.sort(key=lambda item: (item.week, item.content))
    return slots


def _same_activity(left: ScheduleEntry, right: ScheduleEntry) -> bool:
    return (
        left.week == right.week
        and parse_activity(left.content).subject == parse_activity(right.content).subject
        and parse_activity(left.content).teacher == parse_activity(right.content).teacher
        and parse_activity(left.content).room == parse_activity(right.content).room
    )


def _merge_span(
    slots: dict[tuple[str, str], list[ScheduleEntry]],
    day: str,
    start_index: int,
) -> int:
    """Return how many consecutive TIME_ROWS share one full-week activity."""
    start_interval = TIME_ROWS[start_index]
    current = slots.get((day, start_interval), [])
    if len(current) != 1 or current[0].week != WEEK_ALL:
        return 1
    span = 1
    while start_index + span < len(TIME_ROWS):
        nxt = slots.get((day, TIME_ROWS[start_index + span]), [])
        if len(nxt) != 1 or not _same_activity(current[0], nxt[0]):
            break
        span += 1
    return span


def render_schedule_image(
    entries: list[ScheduleEntry],
    group: str,
    week_filter: str = WEEK_ALL,
) -> Image.Image:
    """Draw a colour-coded weekly timetable image for one group."""
    if Image is None or ImageDraw is None or ImageFont is None:
        raise RuntimeError("Lipsește Pillow. Rulează: pip install Pillow")

    slots = _slot_entries(entries, group, week_filter)
    if not any(slots.values()):
        raise ValueError(f"Nu există activități pentru grupa {group}.")

    time_col = 130
    day_col = 210
    header_h = 48
    row_h = 110
    margin = 16
    width = margin * 2 + time_col + day_col * len(DAY_NAMES)
    height = margin * 2 + header_h + row_h * len(TIME_ROWS)

    image = Image.new("RGB", (width, height), COLOR_HEADER)
    draw = ImageDraw.Draw(image)
    title_font = _load_font(16, bold=True)
    cell_font = _load_font(13)
    cell_bold = _load_font(14, bold=True)
    small_font = _load_font(11)

    origin_x = margin
    origin_y = margin

    # Header cells
    headers = ["ORA/ZIUA", *DAY_HEADERS]
    widths = [time_col, *[day_col] * len(DAY_NAMES)]
    x = origin_x
    for label, cell_width in zip(headers, widths):
        box = (x, origin_y, x + cell_width, origin_y + header_h)
        draw.rectangle(box, fill=COLOR_HEADER, outline=COLOR_BORDER, width=2)
        text = _fit_text(draw, label, title_font, cell_width - 10)
        tw = draw.textlength(text, font=title_font)
        draw.text(
            (x + (cell_width - tw) / 2, origin_y + (header_h - title_font.size) / 2 - 2),
            text,
            fill=COLOR_TEXT,
            font=title_font,
        )
        x += cell_width

    occupied: set[tuple[str, int]] = set()
    for row_index, interval in enumerate(TIME_ROWS):
        y = origin_y + header_h + row_index * row_h
        time_box = (origin_x, y, origin_x + time_col, y + row_h)
        draw.rectangle(time_box, fill=COLOR_HEADER, outline=COLOR_BORDER, width=2)
        label = _display_interval(interval)
        tw = draw.textlength(label, font=title_font)
        draw.text(
            (origin_x + (time_col - tw) / 2, y + (row_h - title_font.size) / 2 - 2),
            label,
            fill=COLOR_TEXT,
            font=title_font,
        )

        for day_index, day in enumerate(DAY_NAMES):
            if (day, row_index) in occupied:
                continue
            x = origin_x + time_col + day_index * day_col
            span = _merge_span(slots, day, row_index)
            for offset in range(1, span):
                occupied.add((day, row_index + offset))
            box = (x, y, x + day_col, y + row_h * span)
            cell_entries = slots.get((day, interval), [])

            if not cell_entries:
                _draw_wrapped_block(
                    draw, box, ["-"], COLOR_EMPTY, cell_font, cell_bold,
                )
                continue

            if len(cell_entries) == 1 and (
                cell_entries[0].week == WEEK_ALL or week_filter != WEEK_ALL
            ):
                activity = parse_activity(cell_entries[0].content)
                _draw_wrapped_block(
                    draw,
                    box,
                    _activity_lines(activity),
                    _color_for_kind(activity.kind),
                    cell_font,
                    cell_bold,
                )
                continue

            # Split cell for odd/even week alternatives.
            odd = next((item for item in cell_entries if item.week == WEEK_ODD), None)
            even = next((item for item in cell_entries if item.week == WEEK_EVEN), None)
            all_week = next((item for item in cell_entries if item.week == WEEK_ALL), None)
            if odd or even:
                bands = [odd or all_week, even if even is not None else None]
                if even and not odd and not all_week:
                    bands = [None, even]
                elif odd and not even and not all_week:
                    bands = [odd, None]
            else:
                bands = list(cell_entries)

            band_count = max(len(bands), 1)
            band_h = (row_h * span) / band_count
            for band_index, entry in enumerate(bands):
                band_top = int(y + band_index * band_h)
                band_bottom = int(y + (band_index + 1) * band_h)
                band_box = (x, band_top, x + day_col, band_bottom)
                if entry is None:
                    _draw_wrapped_block(
                        draw, band_box, ["-"], COLOR_EMPTY, small_font, cell_bold,
                    )
                    continue
                activity = parse_activity(entry.content)
                _draw_wrapped_block(
                    draw,
                    band_box,
                    _activity_lines(activity),
                    _color_for_kind(activity.kind),
                    small_font if band_count > 1 else cell_font,
                    cell_bold,
                )

    # Outer border reinforcement
    draw.rectangle(
        (origin_x, origin_y, origin_x + time_col + day_col * len(DAY_NAMES), origin_y + header_h + row_h * len(TIME_ROWS)),
        outline=COLOR_BORDER,
        width=3,
    )
    return image


def save_schedule_image(
    entries: list[ScheduleEntry],
    group: str,
    path: str | Path,
    week_filter: str = WEEK_ALL,
) -> Path:
    destination = Path(path)
    image = render_schedule_image(entries, group, week_filter)
    image.save(destination)
    return destination


class ScheduleApp(ttk.Window):
    def __init__(self) -> None:
        super().__init__(themename=LIGHT_THEME, title="Orar pe grupe")
        self.geometry("1150x700")
        self.minsize(850, 500)
        self.entries: list[ScheduleEntry] = []
        self.dark_mode_var = ttk.BooleanVar(value=False)
        self._build_ui()

    def _configure_fonts(self) -> None:
        colors = self.style.colors
        text_color = "#ffffff" if self.dark_mode_var.get() else "#000000"
        self.style.configure(
            "Title.TLabel",
            font=("Segoe UI", 22, "bold"),
            foreground=text_color,
        )
        self.style.configure(
            "Body.TLabel",
            foreground=text_color,
            font=("Segoe UI", 10),
        )
        self.style.configure(
            "Field.TLabel",
            foreground=text_color,
            font=("Segoe UI", 9, "bold"),
        )
        self.style.configure("Treeview", rowheight=44, font=("Segoe UI", 10))
        self.style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        self.style.configure("Round.Toggle", foreground=text_color)
        self.style.map(
            "Round.Toggle",
            foreground=[("selected", text_color), ("!selected", text_color)],
        )

    def _apply_row_colors(self) -> None:
        colors = self.style.colors
        odd_bg = colors.dark if self.dark_mode_var.get() else colors.light
        self.table.tag_configure("even", background=colors.bg, foreground=colors.fg)
        self.table.tag_configure("odd", background=odd_bg, foreground=colors.fg)
        self.table.tag_configure("day", background=colors.primary, foreground="#ffffff")

    def toggle_theme(self) -> None:
        theme = DARK_THEME if self.dark_mode_var.get() else LIGHT_THEME
        self.style.theme_use(theme)
        self._configure_fonts()
        self._apply_row_colors()

    def _build_ui(self) -> None:
        self._configure_fonts()

        header = ttk.Frame(self, padding=(28, 24, 28, 16))
        header.pack(fill=X)
        card = ttk.Frame(header, padding=(24, 22, 24, 20))
        card.pack(fill=X)
        title_row = ttk.Frame(card)
        title_row.pack(fill=X)
        ttk.Label(title_row, text="Orar pe grupe", style="Title.TLabel").pack(side=LEFT, anchor=W)
        ttk.Checkbutton(
            title_row,
            text="Dark mode",
            variable=self.dark_mode_var,
            command=self.toggle_theme,
            bootstyle="round-toggle",
        ).pack(side=RIGHT)
        ttk.Label(
            card,
            text="Deschide PDF-ul real și afișează activitățile organizate automat pe grupe.",
            style="Body.TLabel",
        ).pack(anchor=W, pady=(4, 18))

        controls = ttk.Frame(card)
        controls.pack(fill=X)
        ttk.Button(
            controls, text="Deschide PDF", command=self.open_pdf, bootstyle="primary",
        ).pack(side=LEFT)
        ttk.Button(
            controls, text="Salvează imagine", command=self.save_image, bootstyle="success",
        ).pack(side=LEFT, padx=(10, 0))
        self.file_label = ttk.Label(
            controls, text="Niciun fișier selectat", style="Body.TLabel",
        )
        self.file_label.pack(side=LEFT, padx=(14, 20))

        ttk.Label(controls, text="GRUPĂ", style="Field.TLabel").pack(side=LEFT, padx=(0, 6))
        self.group_var = ttk.StringVar(value="Toate")
        self.group_box = ttk.Combobox(
            controls, textvariable=self.group_var, state="readonly",
            width=14, bootstyle="primary",
        )
        self.group_box["values"] = ("Toate",)
        self.group_box.pack(side=LEFT)
        self.group_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="ZI", style="Field.TLabel").pack(side=LEFT, padx=(18, 6))
        self.day_var = ttk.StringVar(value="Toate")
        self.day_box = ttk.Combobox(
            controls, textvariable=self.day_var, state="readonly",
            width=12, bootstyle="primary",
        )
        self.day_box["values"] = ("Toate", *DAY_NAMES)
        self.day_box.pack(side=LEFT)
        self.day_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(controls, text="SĂPTĂMÂNĂ", style="Field.TLabel").pack(
            side=LEFT, padx=(18, 6),
        )
        self.week_var = ttk.StringVar(value=WEEK_ALL)
        self.week_box = ttk.Combobox(
            controls, textvariable=self.week_var, state="readonly",
            width=10, bootstyle="primary",
        )
        self.week_box["values"] = (WEEK_ALL, WEEK_ODD, WEEK_EVEN)
        self.week_box.pack(side=LEFT)
        self.week_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        table_frame = ttk.Frame(self, padding=(28, 0, 28, 12))
        table_frame.pack(fill=BOTH, expand=True)
        columns = ("day", "time", "group", "week", "subject", "teacher", "room")
        self.table = ttk.Treeview(
            table_frame, columns=columns, show="headings", bootstyle="primary",
        )
        self._apply_row_colors()
        headings = {
            "day": "Zi",
            "time": "Interval",
            "group": "Grupă",
            "week": "Săptămână",
            "subject": "Obiect",
            "teacher": "Profesor",
            "room": "Sală",
        }
        widths = {
            "day": 110,
            "time": 120,
            "group": 90,
            "week": 100,
            "subject": 280,
            "teacher": 160,
            "room": 140,
        }
        for name in columns:
            self.table.heading(name, text=headings[name], anchor="center")
            self.table.column(name, width=widths[name], anchor="center", stretch=True)
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.table.yview,
            bootstyle="round-primary",
        )
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.pack(side=RIGHT, fill=Y)
        self.status = ttk.Label(
            self, text="Selectează un PDF pentru a vedea orarul.",
            style="Body.TLabel", padding=(28, 0, 28, 18),
        )
        self.status.pack(anchor=W)

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

    def save_image(self) -> None:
        if not self.entries:
            messagebox.showinfo("Salvează imagine", "Deschide mai întâi un PDF cu orarul.")
            return
        group = self.group_var.get()
        if group == "Toate":
            messagebox.showinfo(
                "Salvează imagine",
                "Selectează o grupă anume pentru a genera orarul vizual.",
            )
            return
        path = filedialog.asksaveasfilename(
            title="Salvează orarul ca imagine",
            defaultextension=".png",
            initialfile=f"orar_{group}.png",
            filetypes=(("Imagine PNG", "*.png"), ("Toate fișierele", "*.*")),
        )
        if not path:
            return
        try:
            save_schedule_image(
                self.entries,
                group,
                path,
                week_filter=self.week_var.get(),
            )
        except (RuntimeError, ValueError, OSError) as error:
            messagebox.showerror("Eroare la salvarea imaginii", str(error))
            return
        self.status.configure(text=f"Imaginea a fost salvată: {Path(path).name}")
        messagebox.showinfo("Salvează imagine", f"Orarul pentru {group} a fost salvat.")

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
            self.table.insert(
                "", "end",
                values=(day, "", "", "", "", "", ""),
                tags=("day",),
            )
            for (entry_day, entry_interval, entry_group), alternatives in day_entries:
                alternatives.sort(key=lambda item: (item.week, item.content))
                for item in alternatives:
                    activity = parse_activity(item.content)
                    self.table.insert(
                        "", "end",
                        values=(
                            "",
                            entry_interval,
                            entry_group,
                            item.week,
                            activity.subject,
                            activity.teacher or "—",
                            activity.room or "—",
                        ),
                        tags=("even" if row_index % 2 == 0 else "odd",),
                    )
                    row_index += 1
        self.status.configure(text="Orarul este gata.")


if __name__ == "__main__":
    ScheduleApp().mainloop()
