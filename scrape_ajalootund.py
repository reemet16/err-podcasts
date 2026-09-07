#!/usr/bin/env python3
"""
ERR Arhiivi "Ajalootund R2-s" episoodide kraapija ja RSS-sööda genereerija.
See skript laeb alla kõik Hillar Palametsa "Ajalootund R2-s" saated ERR-i API-st,
tuvastab nende helifailide suurused ja tüübid ning genereerib podcasti-ühilduva
RSS 2.0 XML sööda (feed.xml).

Autor: AI assistent (Cline)
Kuupäev: 06.09.2026
"""

import os
import json
import time
import requests
import email.utils
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# Konstandid
SERIES_SLUG = "ajalootund-r2-s"
SEARCH_API_URL = "https://arhiiv.err.ee/api/v1/search"
DOWNLOAD_API_BASE = "https://arhiiv.err.ee/api/v1/audioDownload"
SERIES_PAGE_URL = f"https://arhiiv.err.ee/audio/seeria/{SERIES_SLUG}"
SERIES_IMAGE_URL = "https://arhiiv-images.err.ee/thumbnails/2023/3528h5172_thumb.jpg"
CACHE_FILE = "episodes_cache.json"
FEED_FILE = "feed.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json"
}

def load_cache():
    """Laeb episoodide puhvri failist, et vältida korduvaid päringuid failisuuruste leidmiseks."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                print(f"Laen puhvri failist: {CACHE_FILE}")
                return json.load(f)
        except Exception as e:
            print(f"Puhvri laadimisel tekkis viga: {e}. Alustan tühja puhvriga.")
    return {}

def save_cache(cache_data):
    """Salvestab episoodide puhvri faili."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        print(f"Puhver salvestatud faili: {CACHE_FILE}")
    except Exception as e:
        print(f"Puhvri salvestamisel tekkis viga: {e}")

def fetch_all_episodes():
    """
    Teeb päringud ERR-i otsingu API-sse ja otsib üles kaikki seeria episoodid.
    Kuna otsingu vastused on piiratud max 500 elemendiga, teeme kaks leheküljepäringut (kokku 670 episoodi).
    """
    print("Otsin seeria 'Ajalootund R2-s' episoode ERR-i arhiivist...")
    all_items = []
    
    # Küsime lehed 1 ja 2
    for page in [1, 2]:
        payload = {
            "queryParams": {
                "phrase": "Ajalootund R2-s",
                "type": "audio",
                "sortOption": "accuracy",
                "page": page,
                "limit": 500,
                "timeRange": "all",
                "timeRangeFrom": None,
                "timeRangeTo": None,
                "includeTranscription": False,
                "advancedParams": []
            }
        }
        
        try:
            r = requests.post(SEARCH_API_URL, headers=HEADERS, json=payload, timeout=15)
            if r.status_code == 200:
                data = r.json()
                groups = data.get("activeList", {}).get("data", [])
                if groups:
                    # Otsingu tulemused on grupeeritud tüübi järgi. Võtame esimese grupi ('audio') andmed
                    items = groups[0].get("data", [])
                    print(f"Leht {page}: Leidsin {len(items)} otsingutulemust.")
                    all_items.extend(items)
                else:
                    print(f"Leht {page}: Otsingutulemusi ei leitud.")
            else:
                print(f"Viga otsingu API päringul (Leht {page}): staatus {r.status_code}")
        except Exception as e:
            print(f"Viga otsingu API päringul (Leht {page}): {e}")
            
    # Filtreerime välja ainult päris saated (role on 'single' ja viitab õigele seeriale)
    episodes = []
    for item in all_items:
        if item.get("role") == "single":
            # Kontrollime, kas kuulub "Ajalootund R2-s" seeria alla
            is_correct_series = False
            for group in item.get("navigationLinks", []):
                if group.get("type") == "series":
                    for nav_item in group.get("data", []):
                        if nav_item.get("url") == f"/audio/seeria/{SERIES_SLUG}":
                            is_correct_series = True
                            break
            if is_correct_series:
                episodes.append(item)
                
    print(f"Kokku leitud sobivaid episoode: {len(episodes)}")
    return episodes

def resolve_audio_info(episode_url):
    """
    Teeb GET päringu voo algusesse (stream=True), et saada kätte meediafaili tegelik suurus ja tüüp.
    Ei laadi alla kogu faili sisu.
    """
    download_url = f"{DOWNLOAD_API_BASE}/{episode_url}"
    try:
        # stream=True tagab, et laaditakse ainult päised
        r = requests.get(download_url, headers={"User-Agent": HEADERS["User-Agent"]}, stream=True, timeout=10)
        length = r.headers.get("Content-Length")
        ctype = r.headers.get("Content-Type")
        r.close()
        
        if length and ctype:
            return {
                "length": int(length),
                "type": ctype
            }
    except Exception:
        pass
        
    return {
        "length": 30000000, # umbes 30 MB vaikeväärtus
        "type": "audio/x-m4a"
    }

def fill_missing_metadata(episodes, cache):
    """
    Täidab puuduvad meediafaili andmed (suurus ja tüüp) paralleelselt ThreadPoolExecutor abil.
    """
    new_episodes = [e for e in episodes if e["url"] not in cache]
    if not new_episodes:
        print("Kõikide episoodide failiandmed on juba puhvris olemas.")
        return cache
        
    print(f"Leidsin {len(new_episodes)} uut episoodi, mille failiandmed on vaja hankida...")
    
    # Paralleelne päringute saatmine (max 30 paralleelset lõime)
    workers = min(30, len(new_episodes))
    print(f"Käivitan päringud {workers} lõimega...")
    
    completed_count = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_url = {
            executor.submit(resolve_audio_info, ep["url"]): ep["url"] 
            for ep in new_episodes
        }
        
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                info = future.result()
                cache[url] = info
            except Exception as e:
                print(f"Viga URL-i {url} töötlemisel: {e}")
                cache[url] = {
                    "length": 30000000,
                    "type": "audio/x-m4a"
                }
                
            completed_count += 1
            if completed_count % 50 == 0 or completed_count == len(new_episodes):
                print(f"  Edukalt hangitud: {completed_count}/{len(new_episodes)} episoodi andmed...")
                
    return cache


def format_pub_date(date_str):
    """Teisendab ISO kuupäeva standardseks podcasti pubDate vorminguks (RFC 822)."""
    if not date_str:
        return email.utils.format_datetime(datetime.now())
    try:
        # Lõikame kuupäeva esimesed 19 märki: YYYY-MM-DDTHH:MM:SS
        dt = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
        return email.utils.format_datetime(dt)
    except Exception:
        try:
            # Fallback asendusena
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return email.utils.format_datetime(dt)
        except Exception:
            return email.utils.format_datetime(datetime.now())

def generate_rss(episodes, cache):
    """
    Genereerib täieliku ja standarditele vastava podcasti RSS 2.0 XML faili.
    Kasutame xml.etree.ElementTree koos itunes nimaruumiga.
    """
    print("Genereerin RSS XML söödet...")
    
    # Nimajooned
    itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    content_ns = "http://purl.org/rss/1.0/modules/content/"
    
    ET.register_namespace("itunes", itunes_ns)
    ET.register_namespace("content", content_ns)
    
    # Sööda juurelement
    rss = ET.Element("rss", version="2.0")
    
    # Kanal
    channel = ET.SubElement(rss, "channel")
    
    # Kanali baasandmed
    title = ET.SubElement(channel, "title")
    title.text = "Ajalootund R2-s"
    
    link = ET.SubElement(channel, "link")
    link.text = SERIES_PAGE_URL
    
    language = ET.SubElement(channel, "language")
    language.text = "et"
    
    copyright_elem = ET.SubElement(channel, "copyright")
    copyright_elem.text = "Eesti Rahvusringhääling (ERR)"
    
    desc = ET.SubElement(channel, "description")
    desc.text = (
        "Ajalootund R2-s on legendaarne raadiosaade, kus Hillar Palamets "
        "viib kuulajad põnevatele rännakutele läbi ajaloo. Saates käsitletakse "
        "erinevaid ajastuid, kultuure, pöördelisi sündmusi ja silmapaistvaid isikuid."
    )
    
    # iTunes spetsiifilised kanali sildid
    author = ET.SubElement(channel, f"{{{itunes_ns}}}author")
    author.text = "Hillar Palamets / ERR"
    
    summary = ET.SubElement(channel, f"{{{itunes_ns}}}summary")
    summary.text = desc.text
    
    image = ET.SubElement(channel, f"{{{itunes_ns}}}image")
    image.set("href", SERIES_IMAGE_URL)
    
    explicit = ET.SubElement(channel, f"{{{itunes_ns}}}explicit")
    explicit.text = "no"
    
    # Kategooria: Society & Culture -> History
    category_parent = ET.SubElement(channel, f"{{{itunes_ns}}}category")
    category_parent.set("text", "Society & Culture")
    category_child = ET.SubElement(category_parent, f"{{{itunes_ns}}}category")
    category_child.set("text", "History")
    
    # Omanik
    owner = ET.SubElement(channel, f"{{{itunes_ns}}}owner")
    owner_name = ET.SubElement(owner, f"{{{itunes_ns}}}name")
    owner_name.text = "Eesti Rahvusringhääling"
    owner_email = ET.SubElement(owner, f"{{{itunes_ns}}}email")
    owner_email.text = "arhiiv@err.ee"
    
    # Sorteerime episoodid kuupäeva järgi kahanevalt (uuemad eespool)
    sorted_episodes = sorted(
        episodes, 
        key=lambda x: x.get("date") or "1970-01-01T00:00:00Z", 
        reverse=True
    )
    
    # Lisame iga episoodi XML-i
    for ep in sorted_episodes:
        url_slug = ep["url"]
        media_info = cache.get(url_slug, {"length": 30000000, "type": "audio/x-m4a"})
        
        item = ET.SubElement(channel, "item")
        
        # Pealkiri
        item_title = ET.SubElement(item, "title")
        item_title.text = ep.get("heading", "Ajalootund R2-s")
        
        itunes_title = ET.SubElement(item, f"{{{itunes_ns}}}title")
        itunes_title.text = ep.get("heading", "Ajalootund R2-s")
        
        # Kirjeldus
        description_text = ep.get("lead") or ep.get("heading") or "Ajalootund R2-s saade."
        
        item_desc = ET.SubElement(item, "description")
        item_desc.text = description_text
        
        item_summary = ET.SubElement(item, f"{{{itunes_ns}}}summary")
        item_summary.text = description_text
        
        # Kuupäev
        pub_date = ET.SubElement(item, "pubDate")
        pub_date.text = format_pub_date(ep.get("date"))
        
        # Viited ja GUID
        item_link = ET.SubElement(item, "link")
        item_link.text = f"https://arhiiv.err.ee/audio/vaata/{url_slug}"
        
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = url_slug
        
        # Enclosure (Otseviide helifailile)
        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", f"{DOWNLOAD_API_BASE}/{url_slug}")
        enclosure.set("type", media_info.get("type", "audio/x-m4a"))
        enclosure.set("length", str(media_info.get("length", 30000000)))
        
        # Pilt (episoodi oma või seeria oma)
        ep_image_url = SERIES_IMAGE_URL
        if ep.get("photoUrl"):
            ep_image_url = f"https://arhiiv-images.err.ee/{ep['photoUrl']}"
            
        item_image = ET.SubElement(item, f"{{{itunes_ns}}}image")
        item_image.set("href", ep_image_url)
        
        item_explicit = ET.SubElement(item, f"{{{itunes_ns}}}explicit")
        item_explicit.text = "no"
        
    # Joondame ja teeme XML-i loetavamaks (pretty-print)
    ET.indent(rss, space="  ")
    
    # Salvestame sööda faili
    tree = ET.ElementTree(rss)
    try:
        tree.write(FEED_FILE, encoding="utf-8", xml_declaration=True)
        print(f"Edu! Podcasti RSS sööt genereeritud faili: {FEED_FILE}")
    except Exception as e:
        print(f"Viga faili kirjutamisel: {e}")


def main():
    start_time = time.time()
    
    # 1. Lae olemasolevate episoodide andmete puhver
    cache = load_cache()
    
    # 2. Otsi ja kraabi kõik episoodid ERR-ist
    try:
        episodes = fetch_all_episodes()
        if not episodes:
            print("Ühtegi episoodi ei leitud. RSS sööta ei looda.")
            return
            
        # 3. Hangi puuduvad meediafailide suurused ja tüübid
        cache = fill_missing_metadata(episodes, cache)
        save_cache(cache)
        
        # 4. Genereeri RSS fail
        generate_rss(episodes, cache)
        
    except KeyboardInterrupt:
        print("\nTöö katkestatud kasutaja poolt. Salvestan vahepuhvri...")
        save_cache(cache)
    except Exception as e:
        print(f"\nSkripti käivitamisel ilmnes ootamatu viga: {e}")
        
    duration = time.time() - start_time
    print(f"Skripti käivitus kestis kokku {duration:.2f} sekundit.")

if __name__ == "__main__":
    main()

