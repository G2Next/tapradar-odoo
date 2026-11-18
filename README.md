# TapRadar Odoo-Addons

Odoo.sh ve Odoo Apps Store için TapRadar eklentileri. Odoo, bu repodaki `17.0`
branch'ini eklenti klasörü olarak kullanır (her eklenti kök dizinde bir klasör).

## tapradar_cashback: Cashback Gift Cards (Cashback-Gutscheine)

Bir kampanya süresince Odoo POS'ta yapılan her alışveriş, müşteriye bir sonraki
alışverişte kullanılacak kişiye özel bir Gutschein kazandırır.

- **Değer:** Odoo yöneticisi seçer: alışverişin yüzdesi (örn. %5 veya %10) ya da
  sabit tutar (örn. 50 € üzeri her alışverişe 5 €).
- **Alışveriş biter bitmez** müşterinin Gutschein'ı oluşur. Kampanya içindeki her
  yeni alışveriş aynı Gutschein'ı artırır, iade azaltır.
- Gutschein **kampanya içinde kullanılamaz**. Kasada okutulursa "erst ab … einlösbar"
  mesajı çıkar; sunucu da harcamayı reddeder.
- **Geçerlilik sonu** yöneticinin girdiği tarih. Başlangıç her zaman son kampanya
  gününden sonraki gün.
- Kasada okutulunca bakiyeyi Odoo düşer (eingelöst).

**Kullanım:** Kasse → Konfiguration → Cashback-Aktionen

1. *Gutscheine & Treue* içinde bu kampanyaya özel **Geschenkkarte** tipinde bir
   program açın. Programın tarihlerini eklenti kendisi ayarlar.
2. Kasse ayarlarında Geschenkkarte seçeneği: **vorhandene Geschenkkarten scannen**.
3. Yeni kampanya: tarihler, yüzde veya sabit tutar, alt sınır, Pfand kategorisi,
   program, geçerlilik sonu → **Aktivieren**.
4. **Vorschau** ile kim ne kadar alıyor görülebilir.

Kurallar:
- Brüt tutar (vergiler dahil), seçilen kategorilerdeki ürünler hariç.
- Sadece müşteri seçilmiş siparişler sayılır (Kundennummer = Kontakt'taki *Referenz*).
- Kasa açıkken POS program listesini yükler: kampanyadan sonraki gün kasayı
  yeniden yükleyin, yoksa Gutschein "geçersiz" görünür.
- Saatte bir çalışan görev kaçan siparişleri yakalar ve biten kampanyayı kapatır.
  Kampanya bittikten sonra mevcut Gutschein'lara dokunulmaz.

Arayüz İngilizce, Almanca çeviri `i18n/de.po` içinde. Bağımlılık: `pos_loyalty`
(Odoo 17 Community + Enterprise). Odoo Online'da çalışmaz.

## Yerel geliştirme

```bash
docker compose up -d          # http://localhost:8069
```

Testler:

```bash
docker compose run --rm odoo odoo -d cashback_test -i tapradar_cashback \
  --test-tags /tapradar_cashback --stop-after-init --without-demo=all
```

## Henüz yok

- TapRadar bağlantısı (Gutschein oluşunca/değişince uygulamaya gönderme)
- POS fişinde TapRadar QR kodu
