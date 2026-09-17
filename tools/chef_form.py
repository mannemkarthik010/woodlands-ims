"""
Build the chef's worksheet as a fillable PDF form.

Every blank is a real AcroForm text field, so the kitchen can type into it in
Preview, Acrobat or any browser, save, and send it back -- rather than printing
it, writing on it, and photographing it.

Run:  python chef_form.py <gaps.json> <out.pdf>
"""

import json
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

INK = colors.HexColor("#1F2933")
GREY = colors.HexColor("#5B6770")
RULE = colors.HexColor("#D8DEE4")
BRAND = colors.HexColor("#35363A")
GOLD = colors.HexColor("#B7791F")
FIELD_BG = colors.HexColor("#FBF9F6")

PAGE_W, PAGE_H = LETTER
LEFT = 18 * mm
RIGHT = PAGE_W - 18 * mm
WIDTH = RIGHT - LEFT


class Sheet:
    def __init__(self, path):
        self.c = canvas.Canvas(path, pagesize=LETTER)
        self.c.setTitle("Woodlands — questions for the kitchen")
        self.c.setAuthor("Woodlands IMS")
        self.c.setSubject("Recipe details still needed from the kitchen")
        self.form = self.c.acroForm
        self.y = PAGE_H - 20 * mm
        self.page = 1
        self.fields = 0

    # -- page furniture ----------------------------------------------------
    def footer(self):
        self.c.setFont("Helvetica", 7.5)
        self.c.setFillColor(GREY)
        self.c.drawString(
            LEFT, 12 * mm, "Woodlands Indian Cuisine · inventory system · questions for the kitchen"
        )
        self.c.drawRightString(RIGHT, 12 * mm, f"Page {self.page}")

    def space(self, needed):
        if self.y - needed < 22 * mm:
            self.footer()
            self.c.showPage()
            self.page += 1
            self.y = PAGE_H - 20 * mm

    def title(self, text, sub=""):
        self.c.setFillColor(BRAND)
        self.c.rect(LEFT, self.y - 2, WIDTH, 3, stroke=0, fill=1)
        self.y -= 14
        self.c.setFont("Helvetica-Bold", 19)
        self.c.setFillColor(INK)
        self.c.drawString(LEFT, self.y - 10, text)
        self.y -= 26
        if sub:
            self.c.setFont("Helvetica", 9.5)
            self.c.setFillColor(GREY)
            for line in sub.split("\n"):
                self.c.drawString(LEFT, self.y, line)
                self.y -= 12
        self.y -= 6

    def heading(self, number, text, note=""):
        self.space(46)
        self.c.setFont("Helvetica-Bold", 12.5)
        self.c.setFillColor(GOLD)
        self.c.drawString(LEFT, self.y, f"{number}.")
        self.c.setFillColor(INK)
        self.c.drawString(LEFT + 14, self.y, text)
        self.y -= 13
        if note:
            self.c.setFont("Helvetica", 9)
            self.c.setFillColor(GREY)
            for line in wrap(note, 108):
                self.c.drawString(LEFT + 14, self.y, line)
                self.y -= 10.5
        self.y -= 4

    def columns(self, labels, widths):
        self.space(20)
        self.c.setFont("Helvetica-Bold", 7.5)
        self.c.setFillColor(GREY)
        x = LEFT
        for label, width in zip(labels, widths, strict=False):
            self.c.drawString(x, self.y, label.upper())
            x += width
        self.y -= 4
        self.c.setStrokeColor(RULE)
        self.c.line(LEFT, self.y, RIGHT, self.y)
        self.y -= 13

    # -- rows --------------------------------------------------------------
    def field(self, name, x, width, tooltip, height=15):
        self.fields += 1
        self.form.textfield(
            name=f"{name}_{self.fields}".replace(" ", "_"),
            tooltip=tooltip,
            x=x,
            y=self.y - 2,
            width=width,
            height=height,
            borderColor=RULE,
            fillColor=FIELD_BG,
            textColor=INK,
            borderWidth=0.6,
            forceBorder=True,
            fontSize=9.5,
        )

    def row(self, label, boxes, label_width, height=15, hint="", blank_label=False):
        """One label and one or more fields on a line."""
        self.space(height + 6)
        if label:
            self.c.setFont("Helvetica", 9.5)
            self.c.setFillColor(INK)
            self.c.drawString(LEFT, self.y + 3, wrap(label, int(label_width / 4.9))[0])
        if blank_label:
            self.c.setStrokeColor(RULE)
            self.c.line(LEFT, self.y, LEFT + label_width - 12, self.y)
        if hint:
            self.c.setFont("Helvetica-Oblique", 8)
            self.c.setFillColor(GOLD)
            self.c.drawRightString(LEFT + label_width - 8, self.y + 3, hint)
        x = LEFT + label_width
        for name, width in boxes:
            self.field(name, x, width, label or "")
            x += width + 6
        self.y -= height + 5

    def block(self, label, name, lines=3):
        """A multi-line field for a method."""
        height = 12 * lines
        wrapped = wrap(label, 96)
        self.space(height + 22 + 12 * len(wrapped))
        self.c.setFont("Helvetica-Bold", 10)
        self.c.setFillColor(INK)
        for line in wrapped:
            self.c.drawString(LEFT, self.y, line)
            self.y -= 12
        self.y -= height - 10
        self.fields += 1
        self.form.textfield(
            name=f"{name}_{self.fields}",
            tooltip=f"How {label} is made",
            x=LEFT,
            y=self.y,
            width=WIDTH,
            height=height,
            borderColor=RULE,
            fillColor=FIELD_BG,
            textColor=INK,
            borderWidth=0.6,
            forceBorder=True,
            fieldFlags="multiline",
            fontSize=9,
        )
        self.y -= 12

    def note(self, text, colour=GREY, size=9):
        self.space(18)
        self.c.setFont("Helvetica-Oblique", size)
        self.c.setFillColor(colour)
        for line in wrap(text, int(WIDTH / (size * 0.47))):
            self.c.drawString(LEFT, self.y, line)
            self.y -= size + 2.5
        self.y -= 4

    def save(self):
        self.footer()
        self.c.save()


def wrap(text, width):
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return lines or [""]


def grouped(measures):
    """Group "spoon of hing" lines by the vessel."""
    from collections import defaultdict

    out = defaultdict(list)
    for line in measures:
        measure, _, ingredient = line.partition(" of ")
        out[measure].append(ingredient)
    return out


def build(data, path):
    sheet = Sheet(path)
    groups = grouped(data["measures"])

    sheet.title(
        "What we still need from the kitchen",
        "Woodlands Indian Cuisine · inventory system · 17 September 2026\n"
        "Type straight into the boxes and save, or print it and write on it.",
    )
    sheet.note(
        "Every box has been left empty on purpose. A plausible number written here by anybody "
        "other than the kitchen would be believed by the system and would be wrong. Rough "
        "answers are fine — “about half a pound”, “four or five bowls”. The first two pages "
        "matter most; if nothing else gets done, do those.",
        colour=INK,
    )

    # ---------------------------------------------------------------- 1
    sheet.heading(
        1,
        "How many servings one batch gives",
        "One number each, and the most useful thing on this sheet. Without it the system "
        "cannot answer “enough for 100 people”, which is the question asked before every "
        "party and every festival day.",
    )
    sheet.columns(["Recipe", "One batch makes", "Servings from that"], [WIDTH - 250, 160, 90])
    for entry in data["servings"]:
        sheet.space(21)
        sheet.c.setFont("Helvetica", 9.5)
        sheet.c.setFillColor(INK)
        sheet.c.drawString(LEFT, sheet.y + 3, entry["title"])
        sheet.c.setFont("Helvetica", 8.5)
        sheet.c.setFillColor(GREY)
        sheet.c.drawString(LEFT + WIDTH - 250, sheet.y + 3, (entry["makes"] or "—")[:34])
        sheet.field(
            f"servings_{entry['title'][:12]}",
            LEFT + WIDTH - 86,
            84,
            f"Servings from one batch of {entry['title']}",
        )
        sheet.y -= 20

    # ---------------------------------------------------------------- 2
    sheet.heading(
        2,
        "The two batters, and the chutneys",
        "Nothing at all is written down for these. The batters matter most — they are what "
        "the whole system was built around.",
    )
    for label, name in [
        ("Dosa batter — urad, rice, dalia, fenugreek per grind, and what one grind makes", "dosa"),
        ("Idly batter — the same", "idly"),
        ("Coconut chutney", "coconut"),
        ("Mint chutney", "mint"),
    ]:
        sheet.block(label, name, lines=4)

    # ---------------------------------------------------------------- 3
    sheet.heading(
        3,
        "What the vessels hold",
        "These are used across many recipes, so one answer each fixes a lot at once.",
    )
    sheet.columns(["Vessel", "It weighs or holds"], [WIDTH - 130, 130])
    vessels = [
        ("1 pot of water", "pot"),
        ("1 large ladle", "ladle"),
        ("1 cap (as used for methi and black pepper)", "cap"),
        ("1 bunch of cilantro", "bunch"),
        ("1 handful of mixed vegetables", "handful"),
        ("1 bucket of sambar", "bucket_sambar", "32 lb — already answered, confirm?"),
        ("1 bucket of rasam", "bucket_rasam"),
        ("1 bucket of basic gravy", "bucket_gravy"),
        ("1 bucket of tomato chutney", "bucket_chutney"),
        ("1 large soup chafer of butter masala concentrate", "chafer"),
        ("1 2.5-inch full pan of kadai sauce", "pan"),
    ]
    for entry in vessels:
        label, name = entry[0], entry[1]
        prefill = entry[2] if len(entry) > 2 else ""
        sheet.row(label, [(name, 124)], WIDTH - 130, hint=prefill)

    # ---------------------------------------------------------------- 4
    sheet.heading(
        4,
        "What a scoop of each of these weighs",
        "A scoop is a vessel, not a weight: a scoop of toor dal is 32 oz and a scoop of sambar "
        "powder is 14 oz. These are the bulk ingredients, where being wrong costs the most.",
    )
    sheet.columns(["A scoop of…", "Weighs"], [WIDTH - 130, 130])
    for ingredient in sorted(groups.get("scoop", [])):
        sheet.row(ingredient, [("scoop", 124)], WIDTH - 130)

    # ---------------------------------------------------------------- 5
    sheet.heading(
        5,
        "Spoons — one question rather than thirty",
        "A spoon of turmeric was weighed at 1.4 oz and a spoon of salt at 2.2 oz. Rather than "
        "weighing a spoon of every spice, please answer the question below. If the answer is "
        "“about the same”, say which figure to use.",
    )
    sheet.block(
        "Is a spoon of most ground spices close to the same weight? Which figure should we use, "
        "and which ones are noticeably different?",
        "spoons",
        lines=4,
    )
    sheet.note(
        f"There are {len(groups.get('spoon', []))} ingredients measured in spoons across the "
        "recipes. If some are very different — a spoon of oil against a spoon of hing — those "
        "are the ones worth weighing separately.",
    )
    sheet.columns(["Any that are different — a spoon of…", "Weighs"], [WIDTH - 130, 130])
    for index in range(6):
        sheet.row("", [(f"spoon_other_{index}", 124)], WIDTH - 130, blank_label=True)

    # ---------------------------------------------------------------- 6
    sheet.heading(
        6,
        "Serving sizes",
        "These are sold both on a plate and in a tub. The tub sizes come from the till; a "
        "plated serving is not written down anywhere.",
    )
    sheet.columns(["Item", "A plated serving is", "Sold in tubs of"], [WIDTH - 250, 160, 90])
    for item, tubs in [
        ("Rasam", "16 oz"),
        ("Chana masala", "4 / 8 / 16 oz"),
        ("Sambar", "8 / 16 oz"),
        ("Dosa batter", "32 oz"),
        ("Idly batter", "32 oz"),
        ("Potato masala", "8 / 16 oz"),
        ("Coconut chutney", "4 / 8 / 16 oz"),
    ]:
        sheet.space(21)
        sheet.c.setFont("Helvetica", 9.5)
        sheet.c.setFillColor(INK)
        sheet.c.drawString(LEFT, sheet.y + 3, item)
        sheet.field(f"serving_size_{item[:10]}", LEFT + WIDTH - 250, 150, f"One plated serving of {item}")
        sheet.c.setFont("Helvetica", 8.5)
        sheet.c.setFillColor(GREY)
        sheet.c.drawRightString(RIGHT, sheet.y + 3, tubs)
        sheet.y -= 20

    # ---------------------------------------------------------------- 7
    sheet.heading(
        7,
        "How each one is made",
        "The document we have gives quantities only. For each: the order things go in, roughly "
        "how long, and how you know when it is right. Rough notes are fine — they will be "
        "typed up and brought back to you for checking.",
    )
    for title in data["methods"]:
        sheet.block(title, "method", lines=4)

    sheet.heading(8, "Anything else worth writing down", "")
    sheet.block("Notes", "notes", lines=6)

    sheet.save()
    return sheet.fields


if __name__ == "__main__":
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
    count = build(data, sys.argv[2])
    print(f"{count} fillable fields written to {sys.argv[2]}")
