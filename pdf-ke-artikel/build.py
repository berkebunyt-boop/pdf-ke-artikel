#!/usr/bin/env python3
"""
Pembuat situs artikel dari PDF.
Satu halaman PDF  ->  satu file HTML bergaya artikel.
Index, sitemap, dan slot iklan dibuat otomatis setiap script dijalankan.

Pakai:  python build.py
"""
import html
import json
import re
import shutil
import statistics
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from string import Template

import pdfplumber

ROOT = Path(__file__).parent

DEFAULTS = {
    "site_name": "Perpustakaan Artikel",
    "site_description": "Kumpulan artikel dari dokumen PDF.",
    "site_url": "",
    "language": "id",
    "pdf_dir": "pdf",
    "output_dir": "dist",
    "part_word": "Bagian",
    "part_style": "word",          # "word" -> Bagian Kedua | "number" -> Bagian 2
    "image_dpi": 130,
    "min_image_pt": 60,
    "show_placeholders": False,    # True = tampilkan kotak iklan kosong untuk uji tata letak
    "inline_every_paragraphs": 4,
    "max_inline_ads": 3,
    "adsense_client": "",
    "head_html": "",
    "body_end_html": "",
    "title_overrides": {},         # contoh: {"nama.pdf": "Soal Latihan Kelas 6"}
    "ad_slots": {
        "top": {"type": "off"},
        "inline": {"type": "off"},
        "bottom": {"type": "off"},
    },
}

ORDINAL = {1: "Pertama", 2: "Kedua", 3: "Ketiga", 4: "Keempat", 5: "Kelima",
           6: "Keenam", 7: "Ketujuh", 8: "Kedelapan", 9: "Kesembilan", 10: "Kesepuluh"}

BULLET = re.compile(r"^([•●▪■◦·‣∙➢➤►▶❖✓✔➔\-–—*]|\(cid:\d+\)|\d{1,3}[.)])\s+")
PAGENUM = re.compile(r"(halaman|page|hal\.?)?\s*\d+(\s*(/|dari|of)\s*\d+)?")
TERMINAL = (".", "!", "?", '"', "”", ":")


# ----------------------------------------------------------------- util
def esc(text):
    return html.escape(text, quote=True)


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:80] or "dokumen"


def prettify(stem):
    return re.sub(r"[-_]+", " ", stem).strip().title()


def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    path = ROOT / "config.json"
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        slots = user.pop("ad_slots", {})
        cfg.update(user)
        cfg["ad_slots"].update(slots)
    return cfg


def part_label(n, cfg):
    if cfg["part_style"] == "word":
        return f"{cfg['part_word']} {ORDINAL.get(n, f'ke-{n}')}"
    return f"{cfg['part_word']} {n}"


def read_template(name):
    return Template((ROOT / "templates" / name).read_text(encoding="utf-8"))


# ----------------------------------------------------------------- iklan
class Ads:
    def __init__(self, cfg):
        self.cfg = cfg
        self.client = cfg["adsense_client"].strip()

    def slot(self, name):
        s = self.cfg["ad_slots"].get(name) or {}
        kind = (s.get("type") or "off").lower()
        if kind == "adsense" and self.client and s.get("slot"):
            return (
                f'<div class="ad ad-{name}"><ins class="adsbygoogle" style="display:block" '
                f'data-ad-client="{esc(self.client)}" data-ad-slot="{esc(str(s["slot"]))}" '
                f'data-ad-format="auto" data-full-width-responsive="true"></ins>'
                f'<script>(adsbygoogle=window.adsbygoogle||[]).push({{}});</script></div>'
            )
        if kind in ("static", "adstera", "html") and s.get("html"):
            return f'<div class="ad ad-{name}">{s["html"]}</div>'
        if self.cfg["show_placeholders"]:
            return f'<div class="ad ad-{name} ad-placeholder">Slot iklan: {name}</div>'
        return ""

    def head(self):
        out = self.cfg["head_html"]
        if self.client:
            out = (
                '<script async src="https://pagead2.googlesyndication.com/pagead/js/'
                f'adsbygoogle.js?client={esc(self.client)}" crossorigin="anonymous"></script>\n'
            ) + out
        return out


# ----------------------------------------------------------------- analisis PDF
def page_lines(page):
    out = []
    for ln in page.extract_text_lines(layout=False, strip=True, return_chars=True):
        chars = [c for c in ln["chars"] if c["text"].strip()]
        text = re.sub(r"\s+", " ", ln["text"]).strip()
        if not chars or not text:
            continue
        size = round(statistics.median(c["size"] for c in chars), 1)
        bold = sum(1 for c in chars if re.search(r"bold|black|heavy", c["fontname"], re.I)) / len(chars) > 0.6
        out.append({"text": text, "x0": ln["x0"], "x1": ln["x1"], "top": ln["top"],
                    "bottom": ln["bottom"], "size": size, "bold": bold})
    return out


def norm(text):
    return re.sub(r"\d+", "#", text.lower()).strip()


def in_margin(line, height):
    return line["bottom"] < height * 0.08 or line["top"] > height * 0.92


def inside(line, bbox):
    cx, cy = (line["x0"] + line["x1"]) / 2, (line["top"] + line["bottom"]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


def join_text(a, b):
    if len(a) > 1 and a.endswith("-") and a[-2].isalpha() and b[:1].islower():
        return a[:-1] + b
    return a + " " + b


def is_heading(line, body):
    t = line["text"]
    if len(t) >= 120 or BULLET.match(t):
        return False
    if line["size"] >= body * 1.15:
        return True
    return line["bold"] and len(t) < 90 and not t.endswith((".", ",", ";"))


def build_blocks(lines, body, page_w):
    """Gabungkan baris-baris menjadi blok: judul (h), paragraf (p), butir daftar (li)."""
    if not lines:
        return []
    left = min(l["x0"] for l in lines)
    right = max(l["x1"] for l in lines)
    blocks, cur = [], None
    for ln in lines:
        size = ln["size"]
        m = BULLET.match(ln["text"])
        if is_heading(ln, body):
            if (cur and cur["kind"] == "h" and abs(cur["size"] - size) < 0.5
                    and ln["top"] - cur["bottom"] < size * 0.8):
                cur["text"] = join_text(cur["text"], ln["text"])
                cur["bottom"], cur["last_x1"] = ln["bottom"], ln["x1"]
                continue
            level = 2 if size >= body * 1.5 else 3
            cur = {"kind": "h", "level": level, "text": ln["text"], "size": size,
                   "top": ln["top"], "bottom": ln["bottom"], "x0": ln["x0"], "last_x1": ln["x1"]}
            blocks.append(cur)
            continue
        if m:
            num = m.group(1)
            cur = {"kind": "li", "ordered": num[0].isdigit(), "delim": num[-1], "num": int(re.sub(r"\D", "", num) or 1),
                   "text": ln["text"], "size": size, "top": ln["top"], "bottom": ln["bottom"],
                   "x0": ln["x0"], "last_x1": ln["x1"]}
            blocks.append(cur)
            continue
        same = False
        if cur and cur["kind"] in ("p", "li"):
            gap = ln["top"] - cur["bottom"]
            indented = ln["x0"] - left > size * 0.9 and cur["text"].endswith(TERMINAL)
            short_end = cur["last_x1"] < right - size * 4 and cur["text"].endswith(TERMINAL)
            same = (gap < size * 0.5 and abs(cur["size"] - size) < 1.5
                    and not indented and not short_end)
        if same:
            cur["text"] = join_text(cur["text"], ln["text"])
            cur["bottom"], cur["last_x1"] = ln["bottom"], ln["x1"]
        else:
            cur = {"kind": "p", "text": ln["text"], "size": size, "top": ln["top"],
                   "bottom": ln["bottom"], "x0": ln["x0"], "last_x1": ln["x1"]}
            blocks.append(cur)
    return blocks


def table_html(rows):
    rows = [[esc((c or "").replace("\n", " ").strip()) for c in r]
            for r in rows if r and any((c or "").strip() for c in r)]
    if not rows:
        return ""
    head = "".join(f"<th>{c}</th>" for c in rows[0])
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows[1:])
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def save_image(block, imgdir, name, dpi):
    imgdir.mkdir(parents=True, exist_ok=True)
    page = block["page"]
    target = page if block.get("full") else page.crop(block["bbox"])
    pic = target.to_image(resolution=dpi).original.convert("RGB")
    pic.save(imgdir / name, "JPEG", quality=82, optimize=True)


def blocks_to_html(blocks, ads, cfg, imgdir, img_url, alt_text, part_no):
    out, open_list, paragraphs, inline_used, img_no = [], None, 0, 0, 0
    last_delim, last_num = None, 0

    def close():
        nonlocal open_list
        if open_list:
            out.append(f"</{open_list}>")
            open_list = None

    every = int(cfg["inline_every_paragraphs"] or 0)
    for idx, b in enumerate(blocks):
        kind = b["kind"]
        if kind == "li":
            tag = "ol" if b["ordered"] else "ul"
            cont = open_list == tag and (
                tag == "ul" or (b["delim"] == last_delim and b["num"] == last_num + 1))
            if not cont:
                close()
                start = f' start="{b["num"]}"' if tag == "ol" and b["num"] != 1 else ""
                out.append(f"<{tag}{start}>")
                open_list = tag
            last_delim, last_num = b.get("delim"), b["num"]
            out.append(f"<li>{esc(BULLET.sub('', b['text'], count=1))}</li>")
            continue
        close()
        if kind == "h":
            out.append(f"<h{b['level']}>{esc(b['text'])}</h{b['level']}>")
        elif kind == "p":
            out.append(f"<p>{esc(b['text'])}</p>")
            paragraphs += 1
            if (every and paragraphs % every == 0 and inline_used < int(cfg["max_inline_ads"])
                    and idx < len(blocks) - 1):
                ad = ads.slot("inline")
                if ad:
                    out.append(ad)
                    inline_used += 1
        elif kind == "table":
            out.append(table_html(b["rows"]))
        elif kind == "img":
            img_no += 1
            name = f"b{part_no}-{img_no}.jpg"
            try:
                save_image(b, imgdir, name, cfg["image_dpi"])
                out.append(f'<figure><img src="{img_url}/{name}" alt="{esc(alt_text)} - gambar {img_no}" '
                           f'loading="lazy"></figure>')
            except Exception as exc:  # gambar rusak tidak boleh menggagalkan build
                print(f"   [lewati gambar] {exc}")
    close()
    return "\n".join(out)


def process_pdf(path, cfg, used_slugs, out_dir, ads):
    with pdfplumber.open(path) as pdf:
        raw = []
        for p in pdf.pages:
            tables = []
            try:
                for t in p.find_tables():
                    rows = t.extract()
                    if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                        tables.append((t.bbox, rows))
            except Exception:
                pass
            raw.append({"page": p, "lines": page_lines(p), "tables": tables,
                        "images": list(p.images), "w": p.width, "h": p.height})
        n = len(raw)

        # buang header/footer yang berulang dan nomor halaman
        repeated = set()
        if n >= 3:
            cnt = Counter()
            for r in raw:
                cnt.update({norm(l["text"]) for l in r["lines"] if in_margin(l, r["h"])})
            repeated = {k for k, v in cnt.items() if v >= max(2, round(n * 0.4))}
        img_cnt = Counter()
        for r in raw:
            img_cnt.update({getattr(im.get("stream"), "objid", None) for im in r["images"]} - {None})
        repeated_img = {k for k, v in img_cnt.items() if n >= 3 and v >= max(2, round(n * 0.4))}

        for r in raw:
            r["lines"] = [l for l in r["lines"]
                          if not (in_margin(l, r["h"])
                                  and (norm(l["text"]) in repeated or PAGENUM.fullmatch(l["text"].lower())))]
            r["lines"] = [l for l in r["lines"] if not any(inside(l, b) for b, _ in r["tables"])]
            keep = []
            for im in r["images"]:
                w, h = im["x1"] - im["x0"], im["bottom"] - im["top"]
                if w < cfg["min_image_pt"] or h < cfg["min_image_pt"] * 0.6:
                    continue
                if getattr(im.get("stream"), "objid", None) in repeated_img:
                    continue
                if w > 0.95 * r["w"] and h > 0.95 * r["h"] and len(r["lines"]) >= 3:
                    continue  # latar belakang halaman
                if max(w, h) / max(min(w, h), 1) > 8:
                    continue  # garis / bingkai tipis
                box = (im["x0"], im["top"], im["x1"], im["bottom"])
                if sum(1 for l in r["lines"] if inside(l, box)) >= 2:
                    continue  # bingkai/latar: teks di atasnya tetap ditampilkan sebagai teks
                keep.append(im)
            r["images"] = keep
            area = sum((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) for im in keep)
            chars = sum(len(l["text"]) for l in r["lines"])
            r["pagemode"] = area >= 0.5 * r["w"] * r["h"] and chars < 300   # halaman scan

        sizes = Counter()
        for r in raw:
            for l in r["lines"]:
                sizes[l["size"]] += len(l["text"])
        body = sizes.most_common(1)[0][0] if sizes else 11

        page_blocks = []
        for r in raw:
            if r["pagemode"]:
                page_blocks.append([{"kind": "img", "top": 0, "full": True, "page": r["page"]}])
                continue
            bl = build_blocks(r["lines"], body, r["w"])
            for bbox, rows in r["tables"]:
                bl.append({"kind": "table", "top": bbox[1], "rows": rows})
            for im in r["images"]:
                bbox = (max(im["x0"], 0.0), max(im["top"], 0.0),
                        min(im["x1"], r["w"]), min(im["bottom"], r["h"]))
                if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                    bl.append({"kind": "img", "top": im["top"], "bbox": bbox, "page": r["page"]})
            bl.sort(key=lambda b: b["top"])
            page_blocks.append(bl)

        # judul = teks terbesar di halaman pertama yang berisi
        title = None
        first = next((i for i, b in enumerate(page_blocks) if b), None)
        if first is not None:
            heads = [b for b in page_blocks[first] if b["kind"] == "h" and len(b["text"]) >= 8]
            if heads:
                biggest = max(b["size"] for b in heads)
                if biggest >= body * 1.3:
                    tb = next(b for b in heads if b["size"] == biggest)
                    title = tb["text"]
                    page_blocks[first].remove(tb)
        meta_title = ((pdf.metadata or {}).get("Title") or "").strip()
        if re.search(r"untitled|^microsoft (word|powerpoint|excel)|\.(docx?|pdf|pptx?)$|^slide ", meta_title, re.I):
            meta_title = ""
        title = cfg.get("title_overrides", {}).get(path.name) or title or meta_title or prettify(path.stem)

        kept = [bl for bl in page_blocks if bl]
        if not kept:
            print(f"   [kosong] {path.name}: tidak ada teks/gambar yang bisa diambil "
                  f"(PDF hasil scan? jalankan OCR dulu)")
            return None

        slug = base = slugify(title)
        k = 2
        while slug in used_slugs:
            slug = f"{base}-{k}"
            k += 1
        used_slugs.add(slug)

        imgdir = out_dir / "img" / slug
        parts = []
        for i, bl in enumerate(kept, 1):
            label = part_label(i, cfg)
            page_title = title if i == 1 else f"{title} - {label}"
            filename = f"{slug}.html" if i == 1 else f"{slug}-{slugify(label)}.html"
            words = sum(len(b["text"].split()) for b in bl if "text" in b)
            first_p = next((b["text"] for b in bl if b["kind"] == "p"), "")
            parts.append({
                "n": i, "label": label, "title": page_title, "filename": filename,
                "words": words, "first_p": first_p,
                "html": blocks_to_html(bl, ads, cfg, imgdir, f"/img/{slug}", page_title, i),
            })
        return {"title": title, "slug": slug, "parts": parts,
                "description": next((p["first_p"] for p in parts if p["first_p"]), "")}


# ----------------------------------------------------------------- tulis HTML
def short(text, limit=155):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def write_pages(docs, cfg, out_dir, ads):
    tpl = read_template("page.html")
    base = cfg["site_url"].rstrip("/")
    year = datetime.now().year
    for doc in docs:
        total = len(doc["parts"])
        parts_list = []
        for p in doc["parts"]:
            parts_list.append((p["label"], p["filename"]))
        for i, p in enumerate(doc["parts"]):
            prev_p = doc["parts"][i - 1] if i > 0 else None
            next_p = doc["parts"][i + 1] if i < total - 1 else None
            nav = '<nav class="pager">'
            nav += (f'<a class="btn" href="/{prev_p["filename"]}">← Sebelumnya</a>' if prev_p else "<span></span>")
            nav += (f'<a class="btn" href="/{next_p["filename"]}">Berikutnya →</a>' if next_p else "<span></span>")
            nav += "</nav>"
            items = "".join(
                '<li{cls}><a href="/{fn}">{lb}</a></li>'.format(
                    cls=' class="now"' if j == i else "", fn=fn, lb=esc(lb))
                for j, (lb, fn) in enumerate(parts_list))
            parts_nav = (f'<nav class="parts"><h2>Semua bagian</h2><ol>{items}</ol></nav>'
                         if total > 1 else "")
            crumb = f'<a href="/">Beranda</a> › '
            crumb += (f'<a href="/{doc["parts"][0]["filename"]}">{esc(doc["title"])}</a> › {esc(p["label"])}'
                      if i > 0 else esc(doc["title"]))
            minutes = max(1, round(p["words"] / 200))
            meta = (f"{p['label']} dari {total} · " if total > 1 else "") + f"{minutes} menit baca"
            desc = short(p["first_p"] or doc["description"] or cfg["site_description"])
            canonical = f'<link rel="canonical" href="{esc(base)}/{p["filename"]}">' if base else ""
            html_out = tpl.safe_substitute(
                lang=esc(cfg["language"]), page_title=esc(p["title"]), site_name=esc(cfg["site_name"]),
                description=esc(desc), canonical_tag=canonical, head_html=ads.head(),
                crumb=crumb, meta_line=esc(meta), ad_top=ads.slot("top"), content=p["html"],
                ad_bottom=ads.slot("bottom"), nav=nav, parts_nav=parts_nav, year=year,
                body_end_html=cfg["body_end_html"])
            (out_dir / p["filename"]).write_text(html_out, encoding="utf-8")


def write_index(docs, cfg, out_dir, ads):
    cards = []
    for doc in sorted(docs, key=lambda d: d["title"].lower()):
        total = len(doc["parts"])
        links = " ".join(f'<a href="/{p["filename"]}">{esc(p["label"])}</a>' for p in doc["parts"])
        cards.append(
            f'<article class="card" data-title="{esc(doc["title"].lower())}">'
            f'<h2><a href="/{doc["parts"][0]["filename"]}">{esc(doc["title"])}</a></h2>'
            f'<p>{esc(short(doc["description"], 180))}</p>'
            f'<p class="meta">{total} bagian</p>'
            + (f'<p class="partlinks">{links}</p>' if total > 1 else "") + "</article>")
    body = "\n".join(cards) or '<p class="empty">Belum ada artikel. Tambahkan file PDF ke folder <code>pdf/</code>.</p>'
    tpl = read_template("index.html")
    (out_dir / "index.html").write_text(tpl.safe_substitute(
        lang=esc(cfg["language"]), site_name=esc(cfg["site_name"]),
        description=esc(cfg["site_description"]), head_html=ads.head(),
        canonical_tag=(f'<link rel="canonical" href="{esc(cfg["site_url"].rstrip("/"))}/">'
                       if cfg["site_url"] else ""),
        ad_top=ads.slot("top"), cards=body, ad_bottom=ads.slot("bottom"),
        year=datetime.now().year, body_end_html=cfg["body_end_html"]), encoding="utf-8")


def write_extras(docs, cfg, out_dir):
    base = cfg["site_url"].rstrip("/")
    if base:
        urls = [f"{base}/"] + [f"{base}/{p['filename']}" for d in docs for p in d["parts"]]
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
               + "\n".join(f"  <url><loc>{esc(u)}</loc></url>" for u in urls) + "\n</urlset>\n")
        (out_dir / "sitemap.xml").write_text(xml, encoding="utf-8")
        (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
    else:
        (out_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    client = cfg["adsense_client"].strip()
    if client:
        pub = client.replace("ca-", "", 1)
        (out_dir / "ads.txt").write_text(f"google.com, {pub}, DIRECT, f08c47fec0942fa0\n")


# ----------------------------------------------------------------- main
def main():
    cfg = load_config()
    ads = Ads(cfg)
    pdf_dir, out_dir = ROOT / cfg["pdf_dir"], ROOT / cfg["output_dir"]
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    shutil.copy(ROOT / "assets" / "style.css", out_dir / "assets" / "style.css")
    static = ROOT / "static"
    if static.exists():
        shutil.copytree(static, out_dir, dirs_exist_ok=True)

    pdfs = sorted((p for p in pdf_dir.glob("*") if p.suffix.lower() == ".pdf"), key=lambda p: p.name.lower())
    print(f"Ditemukan {len(pdfs)} file PDF di '{cfg['pdf_dir']}/'")
    docs, used = [], set()
    for path in pdfs:
        print(f"- {path.name}")
        try:
            doc = process_pdf(path, cfg, used, out_dir, ads)
        except Exception as exc:  # satu PDF bermasalah tidak boleh menggagalkan seluruh situs
            print(f"   [GAGAL] {exc}")
            continue
        if doc:
            print(f"   judul: {doc['title']} ({len(doc['parts'])} bagian)")
            docs.append(doc)

    write_pages(docs, cfg, out_dir, ads)
    write_index(docs, cfg, out_dir, ads)
    write_extras(docs, cfg, out_dir)
    print(f"Selesai: {len(docs)} dokumen, {sum(len(d['parts']) for d in docs)} halaman HTML -> {cfg['output_dir']}/")


if __name__ == "__main__":
    main()
