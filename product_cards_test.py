#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Product card generator for 1C 7.7 DBF exports.

Reads SC354.DBF (products), SC377.DBF (prices), and SC203.DBF (price types).
Photos are matched by the exact filename stem, e.g. 00-11117808.jpg.
"""
from __future__ import annotations

import re
import shutil
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dbfread import DBF
from PIL import Image, ImageDraw, ImageFont, ImageTk

PRICE_TYPE_ID = "N"  # VIP price in SC203/SC377
ALLOWED_GROUPS = {
    "Вафли", "Вода, Сок", "Драже", "Зефир, Птичье молоко", "Конфеты",
    "Крекер, Галеты, Соломка, Хлебцы", "Мармелад", "Печенье", "Пряники",
    "Сухари, Сушка, Луковые кольца",
}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def clean(v):
    return str(v or "").strip()


def font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


@dataclass
class Product:
    code: str
    name: str
    parent: str
    product_id: str
    group: str
    price: float | None
    photo: Path | None
    stock: float


class Catalog:
    def __init__(self, db_dir: Path, photo_dir: Path):
        self.db_dir, self.photo_dir = db_dir, photo_dir
        self.products: list[Product] = []
        self.groups: list[str] = []
        self.refresh()

    def refresh(self):
        products_path = self.db_dir / "SC354.DBF"
        prices_path = self.db_dir / "SC377.DBF"
        types_path = self.db_dir / "SC203.DBF"
        if not products_path.exists():
            raise FileNotFoundError(f"Не найден {products_path}")
        table = DBF(str(products_path), encoding="cp1251", ignore_missing_memofile=True, char_decode_errors="replace")
        if not any(f.name == "SP1113" and f.type == "N" for f in table.fields):
            raise ValueError("В SC354.DBF не найдено числовое поле остатка SP1113. Экспорт остановлен.")
        products = list(table)
        prices = list(DBF(str(prices_path), encoding="cp1251", ignore_missing_memofile=True, char_decode_errors="replace")) if prices_path.exists() else []
        price_types = {clean(r["ID"]): clean(r["DESCR"]) for r in DBF(str(types_path), encoding="cp1251", ignore_missing_memofile=True, char_decode_errors="replace")} if types_path.exists() else {}
        if PRICE_TYPE_ID not in price_types:
            raise ValueError("В SC203.DBF не найден тип VIP-цены (ID N)")

        groups_by_id = {clean(r["ID"]): clean(r["DESCR"]) for r in products if r.get("ISFOLDER") == 1}
        parent_by_id = {clean(r["ID"]): clean(r["PARENTID"]) for r in products if r.get("ISFOLDER") == 1}
        def group_name(parent):
            seen = set()
            while parent and parent not in seen:
                seen.add(parent)
                name = re.sub(r",\s*", ", ", groups_by_id.get(parent, ""))
                if name in ALLOWED_GROUPS:
                    return name
                parent = parent_by_id.get(parent, "")
            return None

        price_by_product = {}
        for r in prices:
            if clean(r["SP381"]) == PRICE_TYPE_ID:
                price_by_product[clean(r["PARENTEXT"])] = float(r["SP382"] or 0)
        photos = {}
        if self.photo_dir.exists():
            for p in self.photo_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    photos[p.stem.lower()] = p

        self.products = []
        for r in products:
            if r.get("ISFOLDER") != 2 or clean(r.get("ISMARK")):
                continue
            code = clean(r["CODE"])
            parent = clean(r["PARENTID"])
            group = group_name(parent)
            stock = r["SP1113"]
            if not code or not group or stock is None or not stock > 0:
                continue
            self.products.append(Product(code, clean(r["DESCR"]), parent, clean(r["ID"]), group, price_by_product.get(clean(r["ID"])), photos.get(code.lower()), stock))
        self.groups = sorted({p.group for p in self.products}, key=str.casefold)


def wrap_text(draw, text, fnt, max_width):
    words = text.split()
    lines, line = [], ""
    for word in words:
        candidate = (line + " " + word).strip()
        if draw.textbbox((0, 0), candidate, font=fnt)[2] <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render_card(product: Product, out: Path, width=1080):
    if not product.stock > 0:
        raise ValueError("нет положительного остатка")
    if not product.photo:
        raise ValueError("нет фото")
    photo = Image.open(product.photo).convert("RGB")
    ratio = width / photo.width
    photo = photo.resize((width, max(1, round(photo.height * ratio))), Image.Resampling.LANCZOS)
    body_font = font(42)
    price_font = font(58, True)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap_text(dummy, product.name, body_font, width - 100)
    footer_h = 80 + len(lines) * 58 + 105
    card = Image.new("RGB", (width, photo.height + footer_h), "white")
    card.paste(photo, (0, 0))
    draw = ImageDraw.Draw(card)
    y = photo.height + 28
    for line in lines:
        draw.text((50, y), line, fill="#20252b", font=body_font)
        y += 58
    price = "Цена: нет данных" if product.price is None else f"Цена: {product.price:g} ₽/кг"
    draw.text((50, y + 20), price, fill="#b3261e" if product.price is not None else "#777", font=price_font)
    out.parent.mkdir(parents=True, exist_ok=True)
    card.save(out, "JPEG", quality=94, optimize=True)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Карточки товаров 1С — только в наличии")
        self.geometry("1100x720")
        self.catalog = None
        self.group_vars = {}
        self.filtered = []
        self.photo_dir = tk.StringVar()
        self.db_dir = tk.StringVar()
        self.search = tk.StringVar()
        self.manufacturer_filter = tk.BooleanVar(value=False)
        self.manufacturer = tk.StringVar(value="Все")
        self.status = tk.StringVar(value="Выберите папку базы и Photo")
        self.build_ui()

    def build_ui(self):
        top = ttk.Frame(self, padding=10); top.pack(fill="x")
        ttk.Button(top, text="Папка базы", command=self.pick_db).grid(row=0, column=0, padx=4)
        ttk.Entry(top, textvariable=self.db_dir, width=58).grid(row=0, column=1, padx=4)
        ttk.Button(top, text="Папка Photo", command=self.pick_photo).grid(row=1, column=0, padx=4, pady=5)
        ttk.Entry(top, textvariable=self.photo_dir, width=58).grid(row=1, column=1, padx=4, pady=5)
        ttk.Button(top, text="Обновить каталог", command=self.load_catalog).grid(row=0, column=2, rowspan=2, padx=10)

        left = ttk.LabelFrame(self, text="Группы 1С", padding=8); left.pack(side="left", fill="y", padx=(10, 5), pady=5)
        self.groups_frame = ttk.Frame(left); self.groups_frame.pack(fill="both", expand=True)
        ttk.Button(left, text="Выбрать все", command=lambda: self.set_groups(True)).pack(fill="x", pady=(8, 2))
        ttk.Button(left, text="Снять все", command=lambda: self.set_groups(False)).pack(fill="x")

        right = ttk.Frame(self, padding=5); right.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(right); controls.pack(fill="x")
        ttk.Entry(controls, textvariable=self.search, width=35).pack(side="left", padx=4)
        ttk.Button(controls, text="Поиск", command=self.apply_filter).pack(side="left")
        ttk.Checkbutton(controls, text="Фильтр по производителю", variable=self.manufacturer_filter, command=self.apply_filter).pack(side="left", padx=15)
        self.manufacturer_box = ttk.Combobox(controls, textvariable=self.manufacturer, state="readonly", width=24)
        self.manufacturer_box.pack(side="left"); self.manufacturer_box.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())

        self.listbox = tk.Listbox(right, selectmode="extended", font=("Arial", 10))
        self.listbox.pack(fill="both", expand=True, pady=8)
        bottom = ttk.Frame(right); bottom.pack(fill="x")
        ttk.Button(bottom, text="Создать выбранные карточки", command=self.generate).pack(side="left")
        ttk.Button(bottom, text="Создать весь список", command=self.generate_all).pack(side="left", padx=8)
        ttk.Label(bottom, textvariable=self.status).pack(side="left", padx=8)

    def pick_db(self):
        p = filedialog.askdirectory(title="Папка с SC354.DBF, SC377.DBF, SC203.DBF")
        if p: self.db_dir.set(p)
    def pick_photo(self):
        p = filedialog.askdirectory(title="Папка с фотографиями")
        if p: self.photo_dir.set(p)
    def load_catalog(self):
        try:
            picked_groups = {g for g, v in self.group_vars.items() if v.get()}
            self.catalog = Catalog(Path(self.db_dir.get()), Path(self.photo_dir.get()))
            for w in self.groups_frame.winfo_children(): w.destroy()
            self.group_vars = {}
            for g in self.catalog.groups:
                v = tk.BooleanVar(value=g in picked_groups); self.group_vars[g] = v
                ttk.Checkbutton(self.groups_frame, text=g, variable=v, command=self.apply_filter).pack(anchor="w")
            self.refresh_manufacturers(); self.apply_filter()
        except Exception as e:
            self.catalog = None
            self.filtered = []
            self.listbox.delete(0, "end")
            self.status.set("Каталог не обновлён")
            messagebox.showerror("Не удалось обновить", str(e))

    def set_groups(self, value):
        for v in self.group_vars.values(): v.set(value)
        self.apply_filter()
    def manufacturer_name(self, name):
        # Conservative display-only helper: manufacturer is the final phrase after the last period.
        parts = [x.strip() for x in re.split(r"\.\s+", name) if x.strip()]
        return parts[-1] if len(parts) > 1 else "Без отдельного производителя"
    def refresh_manufacturers(self):
        vals = sorted({self.manufacturer_name(p.name) for p in self.catalog.products}, key=str.casefold)
        self.manufacturer_box["values"] = ["Все"] + vals
    def apply_filter(self):
        if not self.catalog: return
        selected = {g for g, v in self.group_vars.items() if v.get()}
        q = self.search.get().strip().casefold()
        maker = self.manufacturer.get() if self.manufacturer_filter.get() else "Все"
        self.filtered = [p for p in self.catalog.products if p.group in selected and (not q or q in p.name.casefold() or q in p.code.casefold()) and (maker == "Все" or self.manufacturer_name(p.name) == maker)]
        self.listbox.delete(0, "end")
        for p in self.filtered:
            state = "фото OK" if p.photo else "НЕТ ФОТО"
            price = "цена OK" if p.price is not None else "НЕТ ЦЕНЫ"
            self.listbox.insert("end", f"[{state}; {price}] {p.code} | {p.name} | остаток: {p.stock:g}")
        self.status.set(f"В наличии: {len(self.filtered)}" if selected else "Отметьте нужные группы")
    def generate_all(self): self.generate(indices=range(len(self.filtered)))
    def generate(self, indices=None):
        if not self.catalog: return
        if indices is None: indices = self.listbox.curselection()
        chosen = [self.filtered[i] for i in indices]
        if not chosen: return messagebox.showinfo("Карточки", "Сначала выберите товары или группу")
        folder = filedialog.askdirectory(title="Куда сохранить карточки")
        if not folder: return
        out = Path(folder)
        made = missing_photo = 0
        for p in chosen:
            if not p.photo: missing_photo += 1; continue
            try:
                render_card(p, out / p.group / f"{p.code}.jpg"); made += 1
            except Exception: pass
        self.status.set(f"Создано: {made}; без фото: {missing_photo}")
        messagebox.showinfo("Готово", f"Карточек создано: {made}\nБез фото: {missing_photo}\nПапка: {out}")

if __name__ == "__main__":
    App().mainloop()
