# Tarihsel yöntem tanımları: kaynaklı arşiv kaydı

**Durum:** Tarihsel referans; çalıştırılabilir yöntem değildir  
**İnceleme tarihi:** 2026-10-31  
**Bağlayıcı durum:** Aktif A→B→C, A→T ve MOD5→T kayıtları `OPEN_DEFINITION` olarak kalır. Bu belge onları tanımlamaz, onaylamaz veya etkinleştirmez.

## 1. Bu belge neden var?

Bu kayıt, ChatGPT dışa aktarma arşivinde bulunan eski Super Loto kod açıklamalarındaki sembollerin sürüme göre nasıl kullanıldığını kaybolmaması için saklar. Amaç geçmişi doğru belgelemektir; eski yöntemleri geri yüklemek, kopyalamak, model seçmek veya sonuçlarını yeniden kullanmak değildir.

**Aktif temiz başlangıç kararı korunur.** PR #49 ile doğrulanmamış eski motorlar ve aday puanlayıcılar aktif yoldan çıkarılmıştır. Bu belge eski uygulamalara dair tanıklık niteliğindedir, güncel kanonik yöntem sicili değildir. PR #59 gibi taslak teklifler ayrıca gözden geçirilmeden kanonik tanım sayılmaz.

### Çalışma zamanı sınırı

- `general_method_registry.json` içinde üç aile de `OPEN_DEFINITION`, `canonical_definition: null`, `execution_ready: false` olarak kalmalıdır.
- Bu belge, `general_method_registry.json` veya canlı yöntem tanımı yerine kullanılamaz.
- `general_source_context.py` içindeki izinli kaynak listesi bu belgeyi yüklemiyorsa, GENERAL ajanlarına bu metnin çalışma zamanında verildiği iddia edilemez. Belgede bulunması, ajanların onu otomatik okuduğu anlamına gelmez.
- Bu belgeyi etkin ajan bağlamına eklemek veya tarihsel bir varyantı kanonikleştirmek ayrı bir değişiklik ve gerekli proje onayı gerektirir.
- Hedef çekiliş verisi, eski tahmin çıktıları, tarihsel skorlar ve ayarlanmış ağırlıklar burada saklanmaz.

## 2. T sembolü: tek ve değişmez bir anlamı yok

İncelenen kodlarda `T` en az iki farklı anlamda kullanıldı: bazı eski sürümlerde A'dan B'ye geçiş/dönüşüm vektörü; başka sürümlerde ise geçmiş durum eşleşmelerinden puan toplanan sonraki çekiliş tarafı. Bu kullanımlar tek bir matematiksel tanım olarak birleştirilemez.

### V8.9 — 2026-09-16 — “Branch · süper loto bab”

Kaynak kod açıklamasındaki gösterim:

`A(t) → T(t) → B(t)`

Pozisyon bazında ham dönüşüm:

```python
T[i] = B[i] - A[i]
```

Bu varyantta `T`, hedef çekilişin sayıları veya doğrudan hedef temsili değil, A ile B arasındaki altı pozisyonlu ham fark vektörüdür. A durumunun çeşitli özellikleriyle tarihsel dönüşüm tekrarları araştırılıyordu. Bu kaynak açıklaması, tarihsel bir sürümün semantiğini gösterir; yöntemin başarılı olduğunu kanıtlamaz.

### V12.1 — 2026-09-19 — “süpir”

İki ardışık çekilişin MOD5 gösterimi ve dönüşümü:

```python
A = tuple(x % 5 for x in current["nums"])
B = tuple(x % 5 for x in nxt["nums"])
T = tuple((B[j] - A[j]) % 5 for j in range(6))
```

Burada `T`, her pozisyonda MOD5 durumundan diğerine geçişin mod 5 fark vektörüdür. A'nın MOD5 değerlerinden türetilen pozisyon, ikili/üçlü toplam-fark, genel toplam ve komşuluk özellikleriyle T pozisyonları arasında tekrar eden ilişkiler aranıyordu.

**Önemli ayrım:** Bu tarihsel `MOD5→T` varyantı, sonraki bazı `MOD5_T` isimli aday-oylama yaklaşımlarıyla aynı algoritma değildir.

## 3. V18.0 ve V19.0: `*_T` aile adlarının değişen anlamları

Kaynak sohbet: “Arşiv doğrulama analizi”, 2026-09-23; V18.0 kodu ve daha sonraki V19.0 kod varyantı.

### V18.0

V18'de `state_transition_predict` geçerli çekiliş için bir durum temsili oluşturuyor, geçmişte eşleşen kaynak durumları buluyor ve bunların hemen sonraki çekilişlerinden adaylara oy veriyordu. Kaydedilmiş durum temsilleri:

| Etiket | V18.0'da görülen temsil | Sınır/not |
|---|---|---|
| `MOD5_T` | Altı sayının `n % 5` kalanlarının frekans sayımı | Kalan dağılımı; pozisyon sırasına dayalı fark vektörü değil. |
| `LOW_HIGH_T` | `(count(n <= 30), count(n > 30))` | Eşik 30'dur. |
| `ODD_EVEN_T` | Tek ve çift sayı adetleri | Pozisyon sırasını kullanmaz. |
| `SUM_T` | `(çekiliş toplamı % 5, çekiliş toplamı % 10)` | Bu sürümde ham toplam değildir. |
| `DECADE_T` | `(n - 1) // 10` onluk kategorilerinin frekansları | 1–10, 11–20, …, 51–60 kategorileri. |
| `GAP_T` | Sıralanmış altı sayı arasındaki beş ardışık fark | Kod açıklamasında bu varyantın aday üretme kısıtı not edilmişti. |
| `NUMBER_T` | Kaynak çekilişin sayı/numara dizisi temsili | Aşağıdaki uygulama tutarsızlığı geçerlidir. |

V18.0'da `NUMBER_T` adı gerçek sayı eşleşmesini ima etse de aday puanlayan bölümde `x % 5 == y % 5` kontrolü vardı. Bu nedenle uygulama, gerçek sayı eşleşmesi olarak okunamaz; aynı MOD5 kalanını paylaşan sayılar üzerinden oy aktarabiliyordu. Tarihsel isim ile gerçek kod davranışı ayrılmalıdır.

### V19.0

Daha sonraki kod aynı isimlerin anlamını/algoritmasını değiştirdi:

- `MOD5_T`, `LOW_HIGH_T`, `ODD_EVEN_T` ve `DECADE_T`: tarihsel kaynak çekilişindeki sayı kategorileri ile hemen sonraki çekilişin sayılarını eşleştiren kategori-geçiş puanlaması.
- `NUMBER_T`: kaynak çekilişteki `x` sayısından sonraki çekilişteki `y` sayısına doğrudan sayı→sayı sayımı.
- `SUM_T`: bir varyantta çekiliş toplamının `% 12` durumundan sonraki çekiliş toplamına geçiş; aynı kod ailesinde ayrıca `n % 10` türü sayı-durumu kullanımları görülmüştü. Bunlar birbirine eşitlenmemelidir.
- `GAP_T`: tam GAP imzasını eşleştirmek yerine komşu farklara dayalı benzerlik puanlaması.
- `ABC`: V18'deki MOD5 durum eşleştirmeli üçlü aramadan farklı olarak A ve B'deki sayı çiftlerinden C'deki sayılara sayım.

Bu nedenle `*_T` sonekine bakarak bir algoritma varsaymak doğru değildir. Sürüm, tam kaynak kodu, girdiler ve çıktı hedefi ayrı ayrı belirtilmelidir.

## 4. A→B→C'nin tarihsel varyantları

İncelenen en az iki farklı uygulama vardı:

1. **V18.0:** A ve B'nin MOD5 frekans durumlarını karşılaştırıp geçmiş A→B geçişleri eşleştiriyor, bu üçlülerin C çekilişlerinden aday oyları topluyordu.
2. **V19.0:** Tarihsel üçlülerde A sayısı `x`, B sayısı `y` ve C sayısı `z` arasındaki `(x, y) → z` ilişkilerini sayıyordu.

Bunlar aynı matematiksel yöntem değildir. Daha eski bir dönüşüm motorunda benzer geçişleri eşleştirip B'yi puanlayan yaklaşım da tek başına A→B→C'nin kanonik tanımı sayılamaz.

## 5. A→T'nin tarihsel varyantları

- **V8.9:** `T[i] = B[i] - A[i]` şeklinde ham, pozisyon bazlı geçiş vektörü.
- **Daha sonraki bazı deneyler:** bir durum/özellik anahtarından sonraki çekilişin sayılarına oy veya puan aktarma.

Arşivlenmiş kanıtlar, bunların tek kanonik A→T tanımı olduğunu söylemeye yetmez. Güncel yöntem sicili bu nedenle açık kalmalıdır.

## 6. MOD5'in kendisi de sabit değildi

İncelenen sonraki kod parçalarında hem `n % 5` hem de `(n - 1) % 5` gösterimleri kullanıldı. Bu iki kodlama sınıf etiketlerini bir basamak kaydırır; eşdeğer kabul edilemez.

- V12.1: pozisyon bazlı MOD5 geçiş farkı, `(B[j] - A[j]) % 5`.
- V18.0: altı sayının `n % 5` kalan frekans profili ile durum eşleştirme.
- V19.0 ve daha sonraki varyantlar: kategori bazlı geçiş ya da pozisyon bazlı alternatifler.

V22/V23'e atfedilen sonraki deneylerde farklı pozisyon/kategori düzenleri görüldü; ancak bu belgede tam kaynak sohbet başlığı ve bütün fonksiyon gövdeleri yeniden sabitlenmediği için bunlar tamamlanmış bir tanım gibi sunulmaz. Tam kaynak ve test olmadan ayrıntıyı doldurmak yasaktır.

## 7. Arşiv kanıtı, performans ve hedef izolasyonu

- Bu kayıtlar, dışa aktarma içindeki tarihli kod parçalarının anlamını özetler; yeniden üretilebilir bir kanonik yöntem paketi değildir.
- Eski raporlardaki aday sıralamaları, ağırlıklar ve skorlar bu belgenin parçası değildir ve doğrulanmış tahmin başarısı olarak kullanılamaz.
- Geçmişte hedefin gizli/izole olduğunu söyleyen bir bölümle, aynı kayıt grubunun başka bir yerinde hedefe dair değerlerin görünür olduğu durumlar bulunmuştu. Bu yüzden eski sonuçların hedef izolasyonu ayrıca kanıtlanmadan güvenilir olduğu varsayılmamalıdır.
- Bu belgeye hedef sayıları veya hedefle ilişkili skorlar alınmamıştır.
- Eski sürümdeki kodun semantiğini anlamak, onun sızıntısız test edildiğini veya geleceği tahmin ettiğini ispatlamaz.

## 8. Güncel bağlayıcı durum ve bundan sonraki kapılar

Bu belge, güncel araştırma politikasını değiştirmez:

1. Üç aktif yöntem etiketi `OPEN_DEFINITION` kalır; kanonik tanımlar `null`, çalıştırılabilirlik `false` kalır.
2. Yeni çalışma, sürüme özel tarihsel semantiği evrensel tanıma dönüştürmeden başlamalıdır.
3. Bir yöntemin tam matematiksel tanımı yeni baştan yazılmalı; deterministik sentetik testleri ve hedef/arka plan ayırımı doğrulanmalıdır.
4. İleri kronolojik walk-forward ve zaman sıralı çapraz doğrulama ana değerlendirmedir. Ters kronoloji yalnızca ayrı adlandırılmış geriye dönük stres testidir.
5. Uygun random/null taban çizgisi, belirsizlik ve başarısız sonuçlar raporlanmalıdır. Ortalama eşleşme sayısı tek başına kanıt değildir.
6. Kanonikleştirme veya çalışma zamanına dahil etme için açık kullanıcı onayı gerekir.

## Kaynak dizini

- **V8.9:** “Branch · süper loto bab”, 2026-09-16; `T[i] = B[i] - A[i]`.
- **V12.1:** “süpir”, 2026-09-19; `(B[j] - A[j]) % 5`.
- **V18.0 ve V19.0:** “Arşiv doğrulama analizi”, 2026-09-23; durum imzaları ve sonraki çekiliş oylaması varyantları.
- **V22/V23:** Dışa aktarma içinde sonraki deney kodu parçaları; tam konuşma kaynağı/algoritma doğrulaması bu kayıtta eksiktir ve sonradan kaynaklandırılmalıdır.

Bu kaynak dizini, dışa aktarma arşivindeki görünür sohbet/kod alıntılarına dayanır; tek başına GitHub commit'i, yürütülmüş test sonucu veya bağımsız performans kanıtı anlamına gelmez.
