 CERDAS — CEISA & ERP Data Alignment System

Addon Odoo 18 untuk membandingkan data barang dari file Excel **CEISA 4.0**
dan **IT Inventory**, kemudian menghasilkan laporan rekonsiliasi Excel.
Modul tidak mengubah transaksi inventory/accounting dan tidak melakukan
integrasi langsung dengan API CEISA.

| Informasi | Keterangan |
| --- | --- |
| Technical name | `csl_cerdas` |
| Versi | `18.0.1.0.0` |
| Lisensi | LGPL-3 |
| Dependensi addon | `mail` |
| Library Python | `openpyxl`, `xlsxwriter` (just info karena 2 library sudah masuk ke requirement.txt install odoo) |

## Fitur

- Header rekonsiliasi dengan Periode dan Penanggung Jawab.
- Beberapa pasangan file dalam satu header melalui tab **Rekon Line**.
- Upload, download, ganti, dan hapus file input menggunakan widget Binary standar.
- Tombol **Download Template** untuk mengunduh contoh format input.
- Tombol **Generate** pada setiap baris untuk membandingkan satu pasangan file.
- Laporan `hasil_perbandingan_rekonsiliasi.xlsx` dengan formula kesesuaian
  uraian/satuan dan selisih quantity.
- Chatter, activities, serta tracking perubahan Periode dan Penanggung Jawab.
