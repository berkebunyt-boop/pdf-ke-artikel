# PDF → Artikel HTML (untuk Netlify)

Setiap file PDF di folder `pdf/` diubah menjadi halaman artikel HTML.
Satu halaman PDF = satu file HTML. Index, sitemap, robots.txt, dan ads.txt dibuat otomatis.

## Aturan judul
- Halaman 1: `Judul Artikel`
- Halaman 2: `Judul Artikel - Bagian Kedua`, halaman 3: `... - Bagian Ketiga`, dst.
- Judul diambil dari teks terbesar di halaman pertama PDF (cadangan: metadata PDF, lalu nama file).
- Ingin "Bagian 2" (angka)? Ubah `"part_style": "number"` di `config.json`.

## Cara pakai di Netlify
1. Buat repository GitHub dan unggah seluruh isi folder ini.
2. Di Netlify: **Add new site → Import an existing project** → pilih repository.
   Pengaturan build sudah ada di `netlify.toml` (tidak perlu diubah).
3. Edit `config.json` (nama situs, `site_url`, kode iklan).
4. Menambah artikel: unggah PDF baru ke folder `pdf/` di GitHub.
   Netlify otomatis menjalankan `build.py`, lalu index dan halaman baru terbit sendiri.

Uji di komputer sendiri:
```
pip install -r requirements.txt
python build.py        # hasil ada di folder dist/
```
Catatan: deploy drag-and-drop ke Netlify tidak menjalankan build. Jalankan `python build.py`
dulu, lalu seret folder `dist/` (atau pakai `netlify deploy --prod --dir=dist`).

## Iklan (`config.json`)
Ada 3 posisi: `top` (bawah judul), `inline` (di antara paragraf), `bottom` (bawah artikel).
Tiap posisi punya `type`:
- `"adsense"` + `"slot": "1234567890"` → iklan dinamis Google AdSense (isi juga `adsense_client`: `ca-pub-xxxx`)
- `"adstera"` atau `"static"` + `"html": "<kode dari dashboard>"` → Adstera atau banner statis
- `"off"` → nonaktif

Pengaturan lain:
- `inline_every_paragraphs` / `max_inline_ads`: seberapa sering iklan disisipkan di antara paragraf.
- `head_html` / `body_end_html`: tempat menempel skrip global (mis. Social Bar atau Popunder Adstera).
- `show_placeholders: true`: tampilkan kotak iklan kosong untuk melihat posisi (matikan sebelum publish).
- `adsense_client` terisi → `ads.txt` dibuat otomatis.
- Banner Adstera memakai variabel global per unit iklan, jadi pakai satu unit berbeda untuk tiap posisi,
  dan jangan dipakai di `inline` kalau iklannya akan muncul lebih dari sekali per halaman.

## Batasan
- Terbaik untuk PDF berisi teks dengan satu kolom. PDF multi-kolom bisa urutannya tercampur.
- PDF hasil scan (tanpa teks) perlu di-OCR dulu.
- Judul/subjudul dikenali dari ukuran dan ketebalan huruf, jadi PDF yang formatnya tidak konsisten
  bisa menghasilkan subjudul yang kurang pas.
- Pastikan kamu punya hak untuk mempublikasikan isi PDF, dan situs memenuhi kebijakan AdSense/Adstera.
