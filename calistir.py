"""Günlük akış (üç bölüm):
  1) KEŞİF  : e-postayla başvuru alan ilanları + şirket İK sayfalarını bulur, uygunlara OTOMATİK başvurur
  2) ALARM  : LinkedIn / Kariyer.net iş alarmı e-postalarındaki son 24 saatin ilanlarını puanlayıp sıralar
  3) DİĞER  : Remotive vb. ilan kaynakları (elle başvuru için hazırlanır)
Sonunda takip tablosu güncellenir ve özet e-postası gönderilir.

    python calistir.py            # gerçek
    python calistir.py --kuru     # hiçbir başvuru göndermez; ne gönderileceğini raporlar
    python calistir.py --sahte    # internetsiz test (ornek/ klasöründeki örnek veriler, hiçbir şey gönderilmez)
"""
from __future__ import annotations

import argparse
import os
import json
import re
import sys
import time
import webbrowser

from ai.eslestirici import HAVUZ, KOK, GeminiLLM, SahteLLM, basvuru_hazirla, eslestir, havuzu_kontrol_et, profil_yukle
from toplayici import kaynaklar as k
from types import SimpleNamespace

from ai import kesif_ai, toplu_puan
from toplayici import alarm, eposta, gonderici, kesif, sheets
from toplayici.ozet import ozet_olustur
from toplayici.veritabani import Veritabani

GEMINI_ARA_BEKLEME = 4  # sn; ücretsiz katmanın dakika limitine takılmamak için


def topla(ayar: dict, sahte: bool) -> list[k.Ilan]:
    ilanlar: list[k.Ilan] = []
    if sahte:
        ornek = KOK / "ornek"
        ilanlar += k.google_jobs_ayristir(json.loads((ornek / "serpapi_ornek.json").read_text(encoding="utf-8")))
        ilanlar += k.remotive_ayristir(json.loads((ornek / "remotive_ornek.json").read_text(encoding="utf-8")))
        ilanlar += k.google_web_ayristir(json.loads((ornek / "google_web_ornek.json").read_text(encoding="utf-8")))
        return ilanlar

    for grup in ayar.get("google_web", {}).get("gruplar", []):
        site = k._site(grup["siteler"][0])
        for q in grup.get("sorgular", []):
            try:
                bulunan = k.google_web(q, grup["siteler"], ek=grup.get("ek", ""))
                print(f"  Google/{site} '{q}': {len(bulunan)}")
                ilanlar += bulunan
            except Exception as e:
                print(f"  Google/{site} '{q}' HATA: {e}")
    gj = ayar.get("google_jobs", {})
    for q in (gj.get("sorgular", []) if gj.get("aktif", True) else []):
        try:
            bulunan = k.google_jobs(q, gj.get("konum", ""))
            print(f"  Google Jobs '{q}': {len(bulunan)}")
            ilanlar += bulunan
        except Exception as e:
            print(f"  Google Jobs '{q}' HATA: {e}")
    for q in ayar.get("remotive", {}).get("sorgular", []):
        try:
            bulunan = k.remotive(q)
            print(f"  Remotive '{q}': {len(bulunan)}")
            ilanlar += bulunan
        except Exception as e:
            print(f"  Remotive '{q}' HATA: {e}")
    return ilanlar


def birlestir(ilanlar: list[k.Ilan]) -> list[k.Ilan]:
    """Aynı ilanın farklı kaynaklardaki kopyalarını birleştirir: linkler toplanır, en uzun açıklama kalır."""
    tekil: dict[str, k.Ilan] = {}
    for i in ilanlar:
        if i.id not in tekil:
            tekil[i.id] = i
            continue
        ana = tekil[i.id]
        if len(i.aciklama) > len(ana.aciklama):
            eski_epostalar = ana.epostalar
            ana.aciklama = i.aciklama + ("\n\nİletişim: " + ", ".join(eski_epostalar) if eski_epostalar else "")
        elif i.epostalar:
            ana.aciklama += "\n\nİletişim: " + ", ".join(i.epostalar)
        gorulen = {l["url"] for l in ana.linkler}
        ana.linkler += [l for l in i.linkler if l["url"] not in gorulen]
        if i.kaynak not in ana.kaynak:
            ana.kaynak += f", {i.kaynak}"
    return list(tekil.values())


def on_filtre(ilan: k.Ilan, f: dict) -> bool:
    """True → Gemini'ye gönderilir."""
    baslik = f" {ilan.baslik.lower()} "
    konum = ilan.konum.lower() + " " + ilan.baslik.lower()
    if any(h in konum for h in f.get("konum_haric", [])):
        return False
    if any(h in baslik for h in f.get("baslik_haric", [])):
        return False
    if ilan.kaynak.startswith("google_jobs"):   # sorgular zaten hedefli
        return True
    if ilan.kaynak.startswith("google_web"):    # ham sayfa başlığında ara (şirket adı dahil olabilir)
        m = re.search(r"Sayfa başlığı: (.*)", ilan.aciklama)
        baslik = f" {(m.group(1) if m else ilan.baslik).lower()} "
    return any(kel in baslik for kel in f.get("baslik_kelimeleri", []))


def kanal_belirle(ilan: k.Ilan, sonuc) -> tuple[str, str]:
    """Otomatik başvurulabilirlik sırası: ilandaki e-posta > ATS formu > diğer (manuel).
    AI'ın verdiği e-posta sadece ilan metninde birebir geçiyorsa kabul edilir (uydurma adrese gönderim yok)."""
    if ilan.epostalar:
        return "eposta", ilan.epostalar[0]
    ai_adres = sonuc.basvuru_adresi.strip().lower()
    if sonuc.basvuru_kanali == "eposta" and ai_adres and ai_adres in ilan.metin().lower():
        return "eposta", sonuc.basvuru_adresi.strip()
    if ilan.ats_linki:
        return "ats_form", ilan.ats_linki
    link = ilan.linkler[0]["url"] if ilan.linkler else sonuc.basvuru_adresi
    return "manuel", link


GONDERIM_ARASI = 25     # sn; ardışık başvuru e-postaları arasında (doğal hız, spam koruması)


# ================================================================ 1) KEŞİF
def kesif_bolumu(profil: dict, db: Veritabani, llm, sahte: bool, kuru: bool) -> tuple[list[dict], dict]:
    ay = profil.get("kesif", {})
    rapor = {"sorgu": 0, "sayfa": 0, "eposta_yok": 0, "alakasiz": 0, "zaten_yazildi": 0, "gonderildi": 0, "taslak": 0}
    gonderilenler: list[dict] = []
    if not ay.get("aktif"):
        return gonderilenler, rapor

    if not kuru and not sahte and db.bugun_gonderilen() >= ay.get("gunluk_gonderim", 20):
        print("Bugünün gönderim limiti zaten dolu; keşif atlandı.")
        return gonderilenler, rapor
    adaylar: dict[str, kesif.Aday] = {}
    if sahte:
        ornek = KOK / "ornek"
        for a in kesif.google_ayristir(json.loads((ornek / "kesif_google.json").read_text(encoding="utf-8"))):
            adaylar.setdefault(a.url, a)
        sayfalar = json.loads((ornek / "kesif_sayfalar.json").read_text(encoding="utf-8"))
    else:
        for q, donem in kesif.gunun_sorgulari(ay):
            try:
                bulunan = kesif.google_ara(q, donem)
                rapor["sorgu"] += 1
                print(f"  Keşif '{q}': {len(bulunan)}")
                for a in bulunan:
                    adaylar.setdefault(a.url, a)
            except Exception as e:
                print(f"  Keşif '{q}' HATA: {e}")

    yeni = [a for a in adaylar.values() if not db.kesif_gorulmus_mu(a.url)]
    for a in [a for a in yeni if kesif.atlanacak_mi(a)]:
        db.kesif_kaydet(a.url, "atlandi")
    yeni = [a for a in yeni if not kesif.atlanacak_mi(a)]
    limit_g = ay.get("gunluk_gonderim", 20)
    esik = {"ilan": ay.get("min_puan_ilan", 70), "acik_basvuru": ay.get("min_puan_acik_basvuru", 70)}
    haric = [h.lower() for h in ay.get("haric_kelimeler", [])]
    print(f"Keşif: {len(adaylar)} sayfa, {len(yeni)} yeni. Bugün gönderilen: {db.bugun_gonderilen()}/{limit_g}")

    bu_calisma: set[str] = set()   # aynı çalıştırmada aynı şirkete ikinci kez yazma
    yeni = yeni[: ay.get("max_sayfa", 40)]
    if not sahte:
        kesif.sayfalari_oku(yeni)      # sayfalar (ve gerekirse iletişim/kariyer alt sayfaları) paralel okunur
    for aday in yeni:
        if not kuru and db.bugun_gonderilen() >= limit_g:
            print("  Günlük gönderim limitine ulaşıldı; kalan sayfalar yarına kaldı.")
            break
        if sahte:
            ham = sayfalar.get(aday.url, "")
            aday.metin = kesif._gizli_adresleri_ac(kesif._html_metin(ham)) or aday.ozet
            aday.epostalar = kesif.gecerli_epostalar(kesif.EPOSTA_RE.findall(f"{aday.metin} {aday.ozet}"))
        rapor["sayfa"] += 1
        if not kesif.ilgili_mi(aday, [k.lower() for k in ay.get("anahtarlar", [])]):
            db.kesif_kaydet(aday.url, "eposta_yok")
            rapor["eposta_yok"] += 1
            continue
        try:
            s = kesif_ai.degerlendir(aday, profil, llm)
        except Exception as e:
            print(f"  {aday.alan}: AI HATA {e}")
            continue
        if not sahte:
            time.sleep(GEMINI_ARA_BEKLEME)

        if s.tur == "alakasiz" or s.puan < esik.get(s.tur, 101) or any(h in s.pozisyon.lower() for h in haric):
            db.kesif_kaydet(aday.url, f"alakasiz:{s.tur}", s.puan, s.sirket)
            rapor["alakasiz"] += 1
            elemeler = [u for u in s.uyarilar if not u.startswith(("imza", "ön yazı"))]
            if s.tur != "alakasiz" and s.puan < esik.get(s.tur, 101):
                elemeler.append("puan eşiğin altında")
            if any(h in s.pozisyon.lower() for h in haric):
                elemeler.append("hariç tutulan rol")
            sebep = ", ".join(elemeler) or s.gerekce[:60]
            print(f"  ✘ {s.puan:>3} {s.tur:<12} {s.sirket or aday.alan}  ← {sebep}")
            continue
        anahtar = gonderici.sirket_anahtari(s.eposta)
        if anahtar in bu_calisma or db.yakinda_yazildi_mi(s.eposta, anahtar, ay.get("sirket_bekleme_gun", 90)):
            db.kesif_kaydet(aday.url, "zaten_yazildi", s.puan, s.sirket)
            rapor["zaten_yazildi"] += 1
            continue

        bu_calisma.add(anahtar)
        cv = next(c for c in profil["cv_havuzu"] if c["id"] == s.secilen_cv)
        kayit = {"puan": s.puan, "sirket": s.sirket, "pozisyon": s.pozisyon, "tur": s.tur, "adres": s.eposta,
                 "konu": s.konu, "url": aday.url, "secilen_cv": s.secilen_cv, "gerekce": s.gerekce,
                 "on_yazi": s.on_yazi, "kanal": "eposta_otomatik"}
        if kuru or sahte:
            kayit["durum"] = "Taslak"
            rapor["taslak"] += 1
            print(f"  ◌ {s.puan:>3} {s.tur:<12} {s.sirket} → {s.eposta}  (gönderilmedi: {'test' if sahte else 'kuru çalışma'})")
        else:
            try:
                gonderici.gonder(s.eposta, s.konu, s.on_yazi, HAVUZ / cv["dosya"], profil)
            except Exception as e:
                print(f"  {s.sirket} → {s.eposta}: GÖNDERİM HATA {e}")
                continue
            db.gonderim_kaydet(s.eposta, anahtar, s.sirket, s.tur, s.pozisyon, s.konu, aday.url, s.puan, s.secilen_cv)
            db.kesif_kaydet(aday.url, "gonderildi", s.puan, s.sirket)
            kayit["durum"] = "Başvurdum"
            rapor["gonderildi"] += 1
            print(f"  ✉ {s.puan:>3} {s.tur:<12} {s.sirket} → {s.eposta}")
            time.sleep(GONDERIM_ARASI)
        gonderilenler.append(kayit)
    return gonderilenler, rapor


# ================================================================ 2) ALARM
def alarm_bolumu(profil: dict, db: Veritabani, llm, sahte: bool) -> tuple[list[dict], list[dict], dict]:
    ay = profil.get("alarm", {})
    rapor: dict = {}
    if not ay.get("aktif"):
        return [], [], rapor
    if sahte:
        ornek = KOK / "ornek"
        ilanlar = (alarm.ilanlari_ayikla((ornek / "alarm_linkedin.html").read_text(encoding="utf-8"), "LinkedIn")
                   + alarm.ilanlari_ayikla((ornek / "alarm_kariyer.html").read_text(encoding="utf-8"), "Kariyer.net"))
        rapor = {"LinkedIn": 1, "Kariyer.net": 1}
    else:
        try:
            ilanlar, rapor = alarm.alarm_ilanlari(ay.get("gun", 1))
        except Exception as e:
            print(f"Alarm e-postaları okunamadı: {e}")
            return [], [], rapor
    haric = [h.lower() for h in profil["arama"].get("on_filtre", {}).get("baslik_haric", [])]
    yeni = [i for i in ilanlar if not db.var_mi(i.id) and not any(h in i.baslik.lower() for h in haric)]
    print(f"Alarm: {sum(rapor.values())} e-posta, {len(ilanlar)} ilan, {len(yeni)} yeni.")
    if not yeni:
        return [], [], rapor

    try:
        puanlar = toplu_puan.toplu_puanla(yeni, profil, toplu_puan.SahteTopluLLM() if sahte else llm)
    except Exception as e:
        print(f"Alarm puanlama HATA: {e}")
        return [], [], rapor

    liste = []
    for i in yeni:
        if i.id not in puanlar:
            continue
        puan, gerekce = puanlar[i.id]
        db.kaydet(i, "alarm", SimpleNamespace(puan=puan, secilen_cv=None, gerekce=gerekce), "manuel", i.linkler[0]["url"])
        liste.append({"puan": puan, "pozisyon": i.baslik, "sirket": i.sirket, "konum": i.konum,
                      "url": i.linkler[0]["url"], "kaynak": i.kaynak.split("(")[-1].rstrip(")"), "gerekce": gerekce,
                      "_ilan": i})
    liste.sort(key=lambda x: -x["puan"])

    esik = profil["tercihler"]["min_eslesme_puani"]
    hazirlar = []
    for x in [x for x in liste if x["puan"] >= esik][: ay.get("on_yazi_adet", 5)]:
        try:
            s = eslestir(x["_ilan"].metin(), profil, llm)
        except Exception as e:
            print(f"  Ön yazı HATA ({x['sirket']}): {e}")
            continue
        hazirlar.append({"puan": x["puan"], "sirket": x["sirket"] or s.sirket, "pozisyon": x["pozisyon"],
                         "kanal": "manuel", "adres": x["url"], "secilen_cv": s.secilen_cv, "gerekce": x["gerekce"],
                         "eksikler": s.eksikler, "on_yazi": s.on_yazi})
        if not sahte:
            time.sleep(GEMINI_ARA_BEKLEME)
    for x in liste:
        x.pop("_ilan", None)
    return [x for x in liste if x["puan"] >= ay.get("listede_min_puan", 40)], hazirlar, rapor


# ================================================================ 3) DİĞER KAYNAKLAR (Remotive vb.)
def diger_bolumu(profil: dict, db: Veritabani, llm, sahte: bool) -> tuple[list[dict], dict]:
    ayar = profil["arama"]
    esik = profil["tercihler"]["min_eslesme_puani"]
    tum = topla(ayar, sahte)
    yeni = [i for i in birlestir(tum) if not db.var_mi(i.id) and k.yeterince_yeni(i, ayar.get("max_ilan_yasi_gun", 7))]
    elenen = [i for i in yeni if not on_filtre(i, ayar.get("on_filtre", {}))]
    for i in elenen:
        db.kaydet(i, "on_filtre")
    yeni = [i for i in yeni if i not in elenen]
    sayac = {"hazir": 0, "dusuk_puan": 0, "hata": 0}
    hazirlar: list[dict] = []
    for ilan in yeni[: ayar.get("calistirma_basina_max_eslestirme", 40)]:
        try:
            sonuc = eslestir(ilan.metin(), profil, llm)
        except Exception as e:
            print(f"  {ilan.sirket} – {ilan.baslik}: HATA {e}")
            sayac["hata"] += 1
            continue
        kanal, adres = kanal_belirle(ilan, sonuc)
        ilan.sirket, ilan.baslik = sonuc.sirket or ilan.sirket, sonuc.pozisyon or ilan.baslik
        if sonuc.puan >= esik:
            klasor = basvuru_hazirla(sonuc, profil, ek={"kanal": kanal, "adres": adres, "kaynak": ilan.kaynak})
            db.kaydet(ilan, "hazir", sonuc, kanal, adres, str(klasor))
            hazirlar.append({"puan": sonuc.puan, "sirket": sonuc.sirket, "pozisyon": sonuc.pozisyon, "kanal": kanal,
                             "adres": adres, "secilen_cv": sonuc.secilen_cv, "gerekce": sonuc.gerekce,
                             "eksikler": sonuc.eksikler, "on_yazi": sonuc.on_yazi})
            sayac["hazir"] += 1
        else:
            db.kaydet(ilan, "dusuk_puan", sonuc, kanal, adres)
            sayac["dusuk_puan"] += 1
        if not sahte:
            time.sleep(GEMINI_ARA_BEKLEME)
    print(f"Diğer kaynaklar: {len(yeni)} yeni ilan, {sayac['hazir']} hazır.")
    return hazirlar, sayac


# ================================================================ ana akış
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sahte", action="store_true", help="internetsiz test; hiçbir şey gönderilmez")
    ap.add_argument("--kuru", action="store_true", help="başvuru e-postası göndermez, sadece raporlar")
    ap.add_argument("--sessiz", action="store_true", help="özet sayfasını tarayıcıda açma")
    a = ap.parse_args()

    profil = profil_yukle()
    havuzu_kontrol_et(profil)
    kuru = a.kuru or bool(profil.get("kesif", {}).get("kuru_calisma"))
    db = Veritabani(KOK / "veri" / ("test.db" if a.sahte else "ilanlar.db"))
    llm = SahteLLM() if a.sahte else GeminiLLM()
    kesif_llm = kesif_ai.SahteKesifLLM() if a.sahte else llm

    print("=== 1) Keşif ve otomatik başvuru")
    gonderilenler, kesif_rapor = kesif_bolumu(profil, db, kesif_llm, a.sahte, kuru)
    print("\n=== 2) İş alarmları (LinkedIn / Kariyer.net)")
    alarm_listesi, alarm_hazir, alarm_rapor = alarm_bolumu(profil, db, llm, a.sahte)
    print("\n=== 3) Diğer kaynaklar")
    diger_hazir, sayac = diger_bolumu(profil, db, llm, a.sahte)

    ozet = {"kesif": kesif_rapor, "alarm": alarm_rapor, "diger": sayac, "kuru": kuru or a.sahte}
    print(f"\nÖzet: {kesif_rapor.get('gonderildi', 0)} başvuru gönderildi, {kesif_rapor.get('taslak', 0)} taslak, "
          f"{len(alarm_listesi)} alarm ilanı listelendi, {len(diger_hazir)} diğer hazır.")

    if a.sahte:
        (KOK / "cikti").mkdir(exist_ok=True)
        konu, _, govde = eposta.icerik(gonderilenler, alarm_listesi, alarm_hazir + diger_hazir, ozet, None)
        (KOK / "cikti" / "test_eposta.html").write_text(govde, encoding="utf-8")
        print(f"Test modu: tablo ve e-posta atlandı. E-posta önizlemesi: cikti/test_eposta.html  (konu: {konu})")
        return
    if sheets.aktif_mi():
        try:
            n = sheets.satirlar_ekle(gonderilenler + alarm_hazir + diger_hazir)
            print(f"Takip tablosuna {n} satır eklendi.")
        except Exception as e:
            print(f"Takip tablosu HATA: {e}")
    if eposta.aktif_mi():
        try:
            eposta.gonder(gonderilenler, alarm_listesi, alarm_hazir + diger_hazir, ozet,
                          sheets.tablo_linki() if sheets.aktif_mi() else None)
            print("Özet e-postası gönderildi.")
        except Exception as e:
            print(f"Özet e-postası HATA: {e}")
    if not a.sessiz and not os.environ.get("CI"):
        webbrowser.open(ozet_olustur(db, KOK / "cikti").as_uri())


if __name__ == "__main__":
    sys.exit(main())
