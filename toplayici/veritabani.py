"""Görülen ilanların kaydı (SQLite). Aynı ilan iki kez işlenmez."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SEMA = """
CREATE TABLE IF NOT EXISTS gonderimler (
    tarih TEXT, eposta TEXT, alan TEXT, sirket TEXT, tur TEXT, pozisyon TEXT, konu TEXT, url TEXT, puan INTEGER, cv TEXT
);
CREATE TABLE IF NOT EXISTS kesif (
    url TEXT PRIMARY KEY, tarih TEXT, sonuc TEXT, puan INTEGER, sirket TEXT
);
CREATE TABLE IF NOT EXISTS ilanlar (
    id TEXT PRIMARY KEY,
    baslik TEXT, sirket TEXT, konum TEXT, kaynak TEXT,
    linkler TEXT, epostalar TEXT,
    ilk_gorulme TEXT,
    durum TEXT,            -- dusuk_puan | hazir | basvuruldu | manuel
    puan INTEGER, secilen_cv TEXT, kanal TEXT, adres TEXT, klasor TEXT, gerekce TEXT
);
"""


class Veritabani:
    def __init__(self, yol: Path):
        yol.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(yol)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SEMA)

    def var_mi(self, ilan_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM ilanlar WHERE id=?", (ilan_id,)).fetchone() is not None

    def kaydet(self, ilan, durum: str, sonuc=None, kanal: str = "", adres: str = "", klasor: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO ilanlar VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ilan.id, ilan.baslik, ilan.sirket, ilan.konum, ilan.kaynak,
             json.dumps(ilan.linkler, ensure_ascii=False), json.dumps(ilan.epostalar),
             datetime.now().isoformat(timespec="seconds"), durum,
             getattr(sonuc, "puan", None), getattr(sonuc, "secilen_cv", None),
             kanal, adres, klasor, getattr(sonuc, "gerekce", None)),
        )
        self.db.commit()

    def bugun(self) -> list[sqlite3.Row]:
        gun = datetime.now().date().isoformat()
        return self.db.execute("SELECT * FROM ilanlar WHERE ilk_gorulme LIKE ? ORDER BY puan DESC", (f"{gun}%",)).fetchall()

    # ------------------------------------------------ keşif / gönderim
    def kesif_gorulmus_mu(self, url: str) -> bool:
        return self.db.execute("SELECT 1 FROM kesif WHERE url=?", (url,)).fetchone() is not None

    def kesif_kaydet(self, url: str, sonuc: str, puan: int | None = None, sirket: str = "") -> None:
        self.db.execute("INSERT OR REPLACE INTO kesif VALUES (?,?,?,?,?)",
                        (url, datetime.now().isoformat(timespec="seconds"), sonuc, puan, sirket))
        self.db.commit()

    def yakinda_yazildi_mi(self, eposta: str, alan: str, gun: int) -> bool:
        """Aynı adrese ya da aynı şirket alan adına son `gun` gün içinde gönderim yapıldı mı?"""
        sinir = (datetime.now() - timedelta(days=gun)).isoformat(timespec="seconds")
        return self.db.execute("SELECT 1 FROM gonderimler WHERE tarih>=? AND (eposta=? OR alan=?)",
                               (sinir, eposta, alan)).fetchone() is not None

    def bugun_gonderilen(self) -> int:
        gun = datetime.now().date().isoformat()
        return self.db.execute("SELECT COUNT(*) FROM gonderimler WHERE tarih LIKE ?", (f"{gun}%",)).fetchone()[0]

    def gonderim_kaydet(self, eposta, alan, sirket, tur, pozisyon, konu, url, puan, cv) -> None:
        self.db.execute("INSERT INTO gonderimler VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (datetime.now().isoformat(timespec="seconds"), eposta, alan, sirket, tur, pozisyon,
                         konu, url, puan, cv))
        self.db.commit()
