#!/usr/bin/env python3
"""
ERR Arhiivi "Kuuldemäng" episoodide kraapija ja RSS-sööda genereerija.
See skript laeb alla kõik ERR-i "Kuuldemäng" helifailid ja andmed,
tuvastab nende helifailide suurused paralleelselt ning genereerib neist
podcasti-ühilduva RSS 2.0 XML sööda (feed_kuuldemang.xml), mis laetakse
automaatselt üles GitHub Gistina.

Autor: AI assistent (Cline)
Kuupäev: 06.09.2026
"""

import os
import json
import time
import subprocess
import requests
import email.utils
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET

# Konstandid
SERIES_SLUG = "kuuldemang"
SEARCH_API_URL = "https://arhiiv.err.ee/api/v1/search"
DOWNLOAD_API_BASE = "https://arhiiv.err.ee/api/v1/audioDownload"
SERIES_PAGE_URL = f"https://arhiiv.err.ee/audio/seeria/{SERIES_SLUG}"
SERIES_IMAGE_URL = "https://arhiiv-images.err.ee/thumbnails/2022/502hae6b_thumb.jpg"
CACHE_FILE = "kuuldemang_cache.json"
FEED_FILE = "feed_kuuldemang.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json"
}

def get_github_token():
    """Tuvastab GitHub API tokeni keskkonnamuutujatest või macOS keychainist."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    # Proovime lugeda macOS keychainist
    try:
        res = subprocess.run(
            ["security", "find-internet-password", "-s", "github.com", "-w"],
            capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except Exception:
        return None

def load_cache():
    """Laeb episoodide failisuuruste puhvri failist."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                print(f"Laen puhvri failist: {CACHE_FILE}")
                return json.load(f)
        except Exception as e:
            print(f"Puhvri laadimisel tekkis viga: {e}. Alustan tühja puhvriga.")
    return {}

def save_cache(cache_data):
    """Salvestab puhvri faili."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2, ensure_ascii=False)
        print(f"Puhver salvestatud faili: {CACHE_FILE}")
    except Exception as e:
        print(f"Puhvri salvestamisel tekkis viga: {e}")

def fetch_all_episodes():
    """
    Teeb korduvaid päringuid otsingu API-sse ja otsib üles kõik "Kuuldemäng" seeria episoodid.
    Pagineerib seni, kuni uusi tulemusi enam ei tagastata.
    """
    print("Otsin seeria 'Kuuldemäng' kõiki episoode ERR-i arhiivist...")
    all_items = []
    page = 1
    
    while True:
        payload = {
            "queryParams": {
                "phrase": "KUULDEMÄNG",
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
            r = requests.post(SEARCH_API_URL, headers=HEADERS, json=payload, timeout=20)
            if r.status_code == 200:
                data = r.json()
                groups = data.get("activeList", {}).get("data", [])
                if groups:
                    items = groups[0].get("data", [])
                    if not items:
                        print(f"Leht {page}: Tulemused otsas.")
                        break
                    print(f"Leht {page}: Leidsin {len(items)} otsingutulemust.")
                    all_items.extend(items)
                    page += 1
                else:
                    print(f"Leht {page}: Gruppe ei leitud otsingutulemustes.")
                    break
            else:
                print(f"Viga otsingu API päringul (Leht {page}): staatus {r.status_code}")
                break
        except Exception as e:
            print(f"Viga otsingu API päringul (Leht {page}): {e}")
            break
            
    # Filtreerime välja ainult päris kuuldemängud (role on 'single' ja viitab õigele seeriale)
    episodes = []
    for item in all_items:
        if item.get("role") == "single":
            # Kontrollime, kas kuulub seeria "/audio/seeria/kuuldemang" alla
            is_correct_series = False
            for group in item.get("navigationLinks", []):
                if group.get("type") == "series":
                    for nav_item in group.get("data", []):
                        if nav_item.get("url") == f"/audio/seeria/{SERIES_SLUG}":
                            is_correct_series = True
                            break
            if is_correct_series:
                episodes.append(item)
                
    print(f"Kokku leitud kuuldemängude episoode: {len(episodes)}")
    return episodes

def resolve_audio_info(episode_url):
    """
    Teeb GET päringu voo algusesse (stream=True), et saada kätte meediafaili tegelik suurus ja tüüp.
    """
    download_url = f"{DOWNLOAD_API_BASE}/{episode_url}"
    try:
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
        "length": 45000000, # umbes 45 MB vaikeväärtus kuuldemängule
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
    
    # Suurema nimekirja jaoks võime kasutada kuni 50 paralleelset lõime
    workers = min(50, len(new_episodes))
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
                    "length": 45000000,
                    "type": "audio/x-m4a"
                }
                
            completed_count += 1
            if completed_count % 100 == 0 or completed_count == len(new_episodes):
                print(f"  Edukalt hangitud: {completed_count}/{len(new_episodes)} episoodi andmed...")
                
    return cache

def format_pub_date(date_str):
    """Teisendab ISO kuupäeva standardseks podcasti pubDate vorminguks (RFC 822)."""
    if not date_str:
        return email.utils.format_datetime(datetime.now())
    try:
        dt = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")
        return email.utils.format_datetime(dt)
    except Exception:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return email.utils.format_datetime(dt)
        except Exception:
            return email.utils.format_datetime(datetime.now())

def upload_to_gist(xml_content, filename=FEED_FILE):
    """
    Laeb genereeritud RSS xml sisu üles GitHub Gistina, kasutades tuvastatud tokenit.
    Tagastab otsese (raw) lingi failile.
    """
    token = get_github_token()
    if not token:
        print("HOIATUS: GitHubi tokenit ei leitud. Ei saa faili Gisti laadida.")
        return None
        
    print("Laadin RSS sööda üles GitHub Gistina...")
    url = "https://api.github.com/gists"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    payload = {
        "description": "ERR Kuuldemäng Podcast RSS Feed",
        "public": True,
        "files": {
            filename: {
                "content": xml_content
            }
        }
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 201:
            data = r.json()
            raw_url = data["files"][filename]["raw_url"]
            print("Gist loodi edukalt!")
            return raw_url
        else:
            print(f"Gisti laadimine ebaõnnestus: staatus {r.status_code}, vastus: {r.text}")
    except Exception as e:
        print(f"Viga Gisti laadimisel: {e}")
        
    return None


def upload_to_catbox(file_path):
    """
    Laeb genereeritud RSS xml faili üles Catboxi (https://catbox.moe).
    Tagastab otsese (raw) lingi failile.
    """
    print("Laadin RSS sööda varuvariandina üles Catboxi...")
    url = "https://catbox.moe/user/api.php"
    try:
        with open(file_path, "rb") as f:
            files = {
                "fileToUpload": f
            }
            data = {
                "reqtype": "fileupload"
            }
            r = requests.post(url, data=data, files=files, timeout=30)
            if r.status_code == 200:
                raw_url = r.text.strip()
                print("Catboxi laadimine õnnestus!")
                return raw_url
            else:
                print(f"Catboxi laadimine ebaõnnestus: staatus {r.status_code}")
    except Exception as e:
        print(f"Viga Catboxi laadimisel: {e}")
    return None


def generate_rss(episodes, cache):
    """
    Genereerib täieliku ja standarditele vastava podcasti RSS 2.0 XML faili.
    Kasutame xml.etree.ElementTree koos itunes nimaruumiga.
    """
    print("Genereerin RSS XML söödet...")
    
    itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    content_ns = "http://purl.org/rss/1.0/modules/content/"
    
    ET.register_namespace("itunes", itunes_ns)
    ET.register_namespace("content", content_ns)
    
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    
    # Kanali baasandmed
    title = ET.SubElement(channel, "title")
    title.text = "Raadioteater. Kuuldemängud"
    
    link = ET.SubElement(channel, "link")
    link.text = "https://arhiiv.err.ee/audio/kuuldemang"
    
    language = ET.SubElement(channel, "language")
    language.text = "et"
    
    copyright_elem = ET.SubElement(channel, "copyright")
    copyright_elem.text = "Eesti Rahvusringhääling (ERR)"
    
    desc = ET.SubElement(channel, "description")
    desc.text = (
        "ERR-i Raadioteatri kuuldemängude rikkalik arhiiv. Kuuldemängud, "
        "raadiolavastused, kuuldemängude klassika ja põnevad esitused Eesti parimatelt "
        "näitlejatelt ja lavastajatelt läbi aegade."
    )
    
    author = ET.SubElement(channel, f"{{{itunes_ns}}}author")
    author.text = "Raadioteater / ERR"
    
    summary = ET.SubElement(channel, f"{{{itunes_ns}}}summary")
    summary.text = desc.text
    
    image = ET.SubElement(channel, f"{{{itunes_ns}}}image")
    image.set("href", SERIES_IMAGE_URL)
    
    explicit = ET.SubElement(channel, f"{{{itunes_ns}}}explicit")
    explicit.text = "no"
    
    category_parent = ET.SubElement(channel, f"{{{itunes_ns}}}category")
    category_parent.set("text", "Arts")
    category_child = ET.SubElement(category_parent, f"{{{itunes_ns}}}category")
    category_child.set("text", "Performing Arts")
    
    owner = ET.SubElement(channel, f"{{{itunes_ns}}}owner")
    owner_name = ET.SubElement(owner, f"{{{itunes_ns}}}name")
    owner_name.text = "Raadioteater"
    owner_email = ET.SubElement(owner, f"{{{itunes_ns}}}email")
    owner_email.text = "raadioteater@err.ee"
    
    # Sorteerime episoodid kuupäeva järgi kahanevalt
    sorted_episodes = sorted(
        episodes, 
        key=lambda x: x.get("date") or "1970-01-01T00:00:00Z", 
        reverse=True
    )
    
    for ep in sorted_episodes:
        url_slug = ep["url"]
        media_info = cache.get(url_slug, {"length": 45000000, "type": "audio/x-m4a"})
        
        item = ET.SubElement(channel, "item")
        
        item_title = ET.SubElement(item, "title")
        item_title.text = ep.get("heading", "Kuuldemäng")
        
        itunes_title = ET.SubElement(item, f"{{{itunes_ns}}}title")
        itunes_title.text = ep.get("heading", "Kuuldemäng")
        
        description_text = ep.get("lead") or ep.get("heading") or "ERR Raadioteatri kuuldemäng."
        
        item_desc = ET.SubElement(item, "description")
        item_desc.text = description_text
        
        item_summary = ET.SubElement(item, f"{{{itunes_ns}}}summary")
        item_summary.text = description_text
        
        pub_date = ET.SubElement(item, "pubDate")
        pub_date.text = format_pub_date(ep.get("date"))
        
        item_link = ET.SubElement(item, "link")
        item_link.text = f"https://arhiiv.err.ee/audio/vaata/{url_slug}"
        
        guid = ET.SubElement(item, "guid", isPermaLink="false")
        guid.text = url_slug
        
        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", f"{DOWNLOAD_API_BASE}/{url_slug}")
        enclosure.set("type", media_info.get("type", "audio/x-m4a"))
        enclosure.set("length", str(media_info.get("length", 45000000)))
        
        ep_image_url = SERIES_IMAGE_URL
        if ep.get("photoUrl"):
            ep_image_url = f"https://arhiiv-images.err.ee/{ep['photoUrl']}"
            
        item_image = ET.SubElement(item, f"{{{itunes_ns}}}image")
        item_image.set("href", ep_image_url)
        
        item_explicit = ET.SubElement(item, f"{{{itunes_ns}}}explicit")
        item_explicit.text = "no"
        
    ET.indent(rss, space="  ")
    
    xml_str = ET.tostring(rss, encoding="utf-8")
    xml_declaration = b"<?xml version='1.0' encoding='utf-8'?>\n"
    full_content = xml_declaration + xml_str
    
    try:
        with open(FEED_FILE, "wb") as f:
            f.write(full_content)
        print(f"Podcasti RSS sööt genereeritud kohalikku faili: {FEED_FILE}")
    except Exception as e:
        print(f"Viga faili kirjutamisel: {e}")
        
    return full_content.decode("utf-8")


def main():
    start_time = time.time()
    
    # 1. Lae puhver
    cache = load_cache()
    
    try:
        # 2. Kraabi kõik episoodid
        episodes = fetch_all_episodes()
        if not episodes:
            print("Ühtegi episoodi ei leitud. RSS sööta ei genereerita.")
            return
            
        # 3. Hangi puuduvad meediafailide suurused ja tüübid
        cache = fill_missing_metadata(episodes, cache)
        save_cache(cache)
        
        # 4. Genereeri RSS fail ja sisu
        xml_content = generate_rss(episodes, cache)
        
        # 5. Laadi üles GitHub Gistina
        raw_url = upload_to_gist(xml_content)
        if not raw_url:
            raw_url = upload_to_catbox(FEED_FILE)
            
        if raw_url:
            print(f"\n=======================================================")
            print(f"EDU! Sinu kuuldemängude RSS sööt on avalikult saadaval.")
            print(f"Otselink (Pocket Castsi sisestamiseks):")
            print(f"{raw_url}")
            print(f"=======================================================\n")
        else:
            print("\nLaadimine ebaõnnestus, kuid fail salvestati kohalikult.")
            
    except KeyboardInterrupt:
        print("\nTöö katkestatud. Salvestan vahepuhvri...")
        save_cache(cache)
    except Exception as e:
        print(f"\nSkripti käivitamisel tekkis viga: {e}")
        
    duration = time.time() - start_time
    print(f"Töö kestis kokku {duration:.2f} sekundit.")

if __name__ == "__main__":
    main()

