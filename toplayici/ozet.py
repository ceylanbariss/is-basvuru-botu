"""Günlük özet: bugün hazırlanan başvuruları tek bir HTML sayfasında listeler."""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

CSS = """
:root{--bg:#f6f7f9;--kart:#fff;--yazi:#1d2433;--soluk:#6b7385;--cizgi:#e3e6eb;--yesil:#1f8a4c;--turuncu:#b86a00;--mavi:#2456c9}
@media (prefers-color-scheme:dark){:root{--bg:#15181e;--kart:#1e222a;--yazi:#e6e9ef;--soluk:#9aa3b2;--cizgi:#2e333d;--yesil:#4cc27f;--turuncu:#f0a640;--mavi:#7aa2ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--yazi);font:15px/1.5 "Segoe UI",system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:28px 18px}h1{font-size:22px;margin:0 0 4px}.alt{color:var(--soluk);margin-bottom:22px}
.kart{background:var(--kart);border:1px solid var(--cizgi);border-radius:10px;padding:16px 18px;margin-bottom:14px}
.ust{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.poz{font-weight:600;font-size:16px}.sirket{color:var(--soluk)}
.puan{font-weight:700;font-size:20px;color:var(--yesil);white-space:nowrap}.etiket{display:inline-block;font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--cizgi);margin:8px 6px 0 0;color:var(--soluk)}
.kanal-manuel{color:var(--turuncu);border-color:var(--turuncu)}.kanal-eposta,.kanal-ats_form{color:var(--yesil);border-color:var(--yesil)}
p{margin:10px 0 0}.eksik{color:var(--soluk);font-size:14px}a{color:var(--mavi)}.butonlar{margin-top:12px;display:flex;gap:10px;flex-wrap:wrap}
.buton{border:1px solid var(--cizgi);background:transparent;color:var(--yazi);border-radius:7px;padding:6px 12px;font:inherit;font-size:14px;cursor:pointer;text-decoration:none}
details{margin-top:10px}summary{cursor:pointer;color:var(--soluk);font-size:14px}pre{white-space:pre-wrap;font:inherit;font-size:14px;background:var(--bg);padding:12px;border-radius:8px}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 4px;border-top:1px solid var(--cizgi)}td.p{width:48px;color:var(--soluk)}
"""

JS = """
function kopyala(id,btn){const t=document.getElementById(id).innerText;
 (navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>{btn.textContent='Kopyalandı ✓'})
 .catch(()=>{const r=document.createRange();r.selectNodeContents(document.getElementById(id));const s=getSelection();s.removeAllRanges();s.addRange(r);document.execCommand('copy');btn.textContent='Kopyalandı ✓'})}
"""

KANAL_ADI = {"eposta": "E-posta ile", "ats_form": "Başvuru formu", "manuel": "Elle başvur"}


def _e(s) -> str:
    return html.escape(str(s or ""))


def ozet_olustur(db, cikti_dizini: Path) -> Path:
    satirlar = db.bugun()
    hazir = [r for r in satirlar if r["durum"] == "hazir"]
    dusuk = [r for r in satirlar if r["durum"] == "dusuk_puan"]
    elenen = sum(1 for r in satirlar if r["durum"] == "on_filtre")
    tarih = datetime.now().strftime("%d.%m.%Y")

    kartlar = []
    for n, r in enumerate(hazir):
        klasor = Path(r["klasor"]) if r["klasor"] else None
        veri = {}
        if klasor and (klasor / "sonuc.json").exists():
            veri = json.loads((klasor / "sonuc.json").read_text(encoding="utf-8"))
        on_yazi = veri.get("on_yazi", "")
        eksikler = veri.get("eksikler", [])
        cv = next((p.as_uri() for p in klasor.glob("*_CV.pdf")), "#") if klasor else "#"
        adres = r["adres"] or ""
        link = f"mailto:{adres}" if r["kanal"] == "eposta" else adres
        kartlar.append(f"""
<div class="kart"><div class="ust"><div><div class="poz">{_e(r['baslik'])}</div><div class="sirket">{_e(r['sirket'])}</div></div>
<div class="puan">{r['puan']}</div></div>
<span class="etiket kanal-{_e(r['kanal'])}">{KANAL_ADI.get(r['kanal'], r['kanal'])}</span><span class="etiket">{_e(r['kaynak'])}</span><span class="etiket">CV: {_e(r['secilen_cv'])}</span>
<p>{_e(r['gerekce'])}</p>
{f'<p class="eksik">Eksikler: {_e("; ".join(eksikler))}</p>' if eksikler else ''}
<div class="butonlar"><a class="buton" href="{_e(link)}" target="_blank">İlana git ↗</a><a class="buton" href="{cv}" target="_blank">CV'yi aç</a>
<button class="buton" onclick="kopyala('oy{n}',this)">Ön yazıyı kopyala</button></div>
<details><summary>Ön yazı</summary><pre id="oy{n}">{_e(on_yazi)}</pre></details></div>""")

    dusuk_tablo = "".join(f"<tr><td class='p'>{r['puan']}</td><td>{_e(r['baslik'])} – {_e(r['sirket'])}</td></tr>" for r in dusuk)
    sayfa = f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Başvuru özeti {tarih}</title><style>{CSS}</style></head><body><main>
<h1>Başvuru özeti · {tarih}</h1>
<div class="alt">{len(hazir)} başvuru hazır · {len(dusuk)} düşük puan · {elenen} ön filtrede elendi</div>
{''.join(kartlar) or '<div class="kart">Bugün eşiği geçen ilan yok.</div>'}
{f'<details class="kart"><summary>Düşük puanlı ilanlar ({len(dusuk)})</summary><table>{dusuk_tablo}</table></details>' if dusuk else ''}
</main><script>{JS}</script></body></html>"""
    yol = cikti_dizini / f"ozet_{datetime.now():%Y-%m-%d}.html"
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text(sayfa, encoding="utf-8")
    return yol
