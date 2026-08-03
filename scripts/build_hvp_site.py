#!/usr/bin/env python3
"""Build Kolleega subject pages from YTL final grading-instruction pages."""
from __future__ import annotations

import html
import re
import shutil
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from pypdf import PdfReader

BASE = "https://www.ylioppilastutkinto.fi"
INDEX_URL = BASE + "/fi/tutkinnon-suorittaminen/hyvan-vastauksen-piirteet"
SITE_URL = "https://mikaset.github.io/kolleega"
TARGET_TERMS = [
    (2026, "kevat"),
    (2025, "syksy"),
    (2025, "kevat"),
    (2024, "syksy"),
    (2024, "kevat"),
    (2023, "syksy"),
    (2023, "kevat"),
    (2022, "syksy"),
    (2022, "kevat"),
    (2021, "syksy"),
]
SKIP_WORDS = ("näkövamma", "kuulovamma", "ei julkaista")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Kolleega-HVP-builder/1.0 (+https://mikaset.github.io/kolleega/)"})


@dataclass(frozen=True)
class Exam:
    year: int
    term: str
    label: str
    subject: str
    slug: str
    source_url: str
    text: str


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = norm(value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def clean_label(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r",?\s*tavanomainen koe\b", "", value, flags=re.I)
    return value.strip(" ,")


def subject_identity(section: str, link_text: str) -> tuple[str, str] | None:
    section_n = norm(section)
    text = clean_label(link_text)
    text_n = norm(text)
    if any(word in text_n for word in SKIP_WORDS):
        return None

    if "aidinkieli ja kirjallisuus" in section_n and "saamen" not in section_n:
        if "lukutaidon" in text_n:
            return "Äidinkieli ja kirjallisuus – lukutaidon koe", "aidinkieli-ja-kirjallisuus-lukutaidon-koe"
        if "kirjoitustaidon" in text_n:
            return "Äidinkieli ja kirjallisuus – kirjoitustaidon koe", "aidinkieli-ja-kirjallisuus-kirjoitustaidon-koe"
        if "suomi toisena" in text_n:
            return "Suomi toisena kielenä ja kirjallisuus", "suomi-toisena-kielena-ja-kirjallisuus"

    if "vieras kieli" in section_n:
        length = "pitka" if "pitka" in section_n else "lyhyt"
        language = re.split(r"[,()]", text, maxsplit=1)[0].strip()
        if not language:
            return None
        display = f"{language} – {'pitkä' if length == 'pitka' else 'lyhyt'} oppimäärä"
        extra = ""
        if "laajempi lyhyt" in text_n:
            display = f"{language} – laajempi lyhyt oppimäärä"
            extra = "-laajempi"
        return display, f"{slugify(language)}{extra}-{length}-oppimaara"

    if "matematiikka" in section_n:
        if "pitka" in text_n:
            return "Matematiikka – pitkä oppimäärä", "matematiikka-pitka-oppimaara"
        if "lyhyt" in text_n:
            return "Matematiikka – lyhyt oppimäärä", "matematiikka-lyhyt-oppimaara"

    if "toinen kotimainen" in section_n:
        if "keskipitka" in text_n:
            return "Ruotsi – keskipitkä oppimäärä", "ruotsi-keskipitka-oppimaara"
        if "pitka" in text_n:
            return "Ruotsi – pitkä oppimäärä", "ruotsi-pitka-oppimaara"

    direct = {
        "biologia": ("Biologia", "biologia"),
        "filosofia": ("Filosofia", "filosofia"),
        "historia": ("Historia", "historia"),
        "fysiikka": ("Fysiikka", "fysiikka"),
        "psykologia": ("Psykologia", "psykologia"),
        "elamankatsomustieto": ("Elämänkatsomustieto", "elamankatsomustieto"),
        "yhteiskuntaoppi": ("Yhteiskuntaoppi", "yhteiskuntaoppi"),
        "kemia": ("Kemia", "kemia"),
        "maantiede": ("Maantiede", "maantiede"),
        "terveystieto": ("Terveystieto", "terveystieto"),
    }
    for key, result in direct.items():
        if text_n == key:
            return result
    if "evankelisluterilainen" in text_n:
        return "Evankelisluterilainen uskonto", "evankelisluterilainen-uskonto"
    if "ortodoksinen" in text_n:
        return "Ortodoksinen uskonto", "ortodoksinen-uskonto"

    if "saamen aidinkieli" in section_n:
        language = clean_label(text)
        return f"{language} – äidinkieli ja kirjallisuus", f"{slugify(language)}-aidinkieli-ja-kirjallisuus"

    return None


def get(url: str, *, timeout: int = 60) -> requests.Response:
    last: Exception | None = None
    for attempt in range(4):
        try:
            response = SESSION.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Lataus epäonnistui: {url}: {last}")


def discover_season_pages() -> dict[tuple[int, str], str]:
    soup = BeautifulSoup(get(INDEX_URL).text, "html.parser")
    found: dict[tuple[int, str], str] = {}
    for anchor in soup.find_all("a", href=True):
        label = norm(anchor.get_text(" ", strip=True))
        match = re.fullmatch(r"(kevat|syksy)\s+(20\d{2})", label)
        if not match:
            continue
        term, year_s = match.groups()
        key = (int(year_s), term)
        if key in TARGET_TERMS:
            found[key] = urljoin(INDEX_URL, anchor["href"])
    missing = [key for key in TARGET_TERMS if key not in found]
    if missing:
        raise RuntimeError(f"Kausisivuja ei löytynyt: {missing}")
    return found


def previous_heading(anchor: Tag) -> str:
    heading = anchor.find_previous(["h2", "h3"])
    return heading.get_text(" ", strip=True) if heading else ""


def grading_links(page_url: str) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(get(page_url).text, "html.parser")
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        host = urlparse(href).netloc.casefold()
        if "tiedostot.ylioppilastutkinto.fi" not in host and not href.casefold().endswith(".pdf"):
            continue
        text = anchor.get_text(" ", strip=True)
        if not text or any(word in norm(text) for word in SKIP_WORDS):
            continue
        if href in seen:
            continue
        seen.add(href)
        results.append((previous_heading(anchor), text, href))
    return results


def extract_source(url: str) -> str:
    response = get(url, timeout=120)
    content_type = response.headers.get("content-type", "").casefold()
    if "pdf" in content_type or url.casefold().endswith(".pdf"):
        from io import BytesIO
        reader = PdfReader(BytesIO(response.content))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        response.encoding = response.apparent_encoding or response.encoding
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup(["script", "style", "nav", "footer", "noscript"]):
            node.decompose()
        main = soup.select_one("main") or soup.select_one("article") or soup.body or soup
        text = main.get_text("\n", strip=True)
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 200:
        raise RuntimeError(f"HVP-teksti jäi epäilyttävän lyhyeksi: {url}")
    return text


def term_label(year: int, term: str) -> str:
    return f"{'Kevät' if term == 'kevat' else 'Syksy'} {year}"


def page_template(subject: str, slug: str, exams: list[Exam]) -> str:
    toc = "\n".join(f'<li><a href="#koe-{i}">{html.escape(exam.label)}</a></li>' for i, exam in enumerate(exams, 1))
    sections = []
    for i, exam in enumerate(exams, 1):
        sections.append(f'''<section class="exam" id="koe-{i}">
<h2>{html.escape(exam.label)}</h2>
<p class="source"><a href="{html.escape(exam.source_url, quote=True)}" rel="external">Avaa alkuperäinen YTL:n HVP</a></p>
<pre class="hvp">{html.escape(exam.text)}</pre>
</section>''')
    canonical = f"{SITE_URL}/{slug}/"
    return f'''<!doctype html>
<html lang="fi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="index,follow"><meta name="description" content="{html.escape(subject)}: kymmenen uusinta ylioppilaskokeen hyvän vastauksen piirteet -aineistoa.">
<link rel="canonical" href="{canonical}"><title>{html.escape(subject)} – Kolleega HVP</title>
<style>:root{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#172033;background:#f5f7fb}}*{{box-sizing:border-box}}body{{margin:0;line-height:1.55}}header,main,footer{{width:min(1100px,calc(100% - 2rem));margin:auto}}header{{padding:2.5rem 0 1.2rem}}h1{{font-size:clamp(2rem,6vw,3.6rem);line-height:1.08}}.agent,.toc,.exam{{background:#fff;border:1px solid #d9dfeb;border-radius:.8rem;padding:1.1rem;margin:1.2rem 0}}.agent{{background:#eaf2ff;border-left:5px solid #2855b6}}.toc ol{{columns:2;column-width:240px}}.hvp{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}.source{{color:#59657c}}a{{color:#194ca5}}footer{{padding:2rem 0 3rem;color:#59657c}}@media(max-width:650px){{.toc ol{{columns:1}}}}</style></head>
<body><header><p><a href="../">Kolleegan pääsivulle</a></p><h1>{html.escape(subject)}</h1>
<p>Kymmenen uusinta ylioppilaskokeen hyvän vastauksen piirteet -aineistoa kokonaisina tekstikoosteina.</p>
<div class="agent"><strong>Ohje agentille:</strong> Käytä tämän sivun aineistoa, kun kysymys koskee oppiainetta {html.escape(subject)}. Kerro, mihin kokeeseen tulkinta perustuu.</div></header>
<main><nav class="toc"><h2>Kokeet</h2><ol>{toc}</ol></nav>{''.join(sections)}</main>
<footer>Aineisto: Ylioppilastutkintolautakunnan lopulliset hyvän vastauksen piirteet.</footer></body></html>'''


def root_template(groups: dict[str, list[Exam]]) -> str:
    cards = []
    for slug, exams in sorted(groups.items(), key=lambda item: item[1][0].subject.casefold()):
        cards.append(f'<a class="card" href="{slug}/"><h2>{html.escape(exams[0].subject)}</h2><p>{len(exams)} koetta · {html.escape(exams[0].label)}–{html.escape(exams[-1].label)}</p></a>')
    return f'''<!doctype html><html lang="fi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="index,follow"><meta name="description" content="Kolleega kokoaa lukion oppiaineiden kymmenen uusinta ylioppilaskokeen hyvän vastauksen piirrettä."><link rel="canonical" href="{SITE_URL}/"><title>Kolleega – ylioppilaskokeiden HVP-aineisto</title><style>:root{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#172033;background:#f5f7fb}}*{{box-sizing:border-box}}body{{margin:0;line-height:1.55}}header,main,footer{{width:min(1120px,calc(100% - 2rem));margin:auto}}header{{padding:3rem 0 1.5rem}}h1{{font-size:clamp(2rem,6vw,4rem);line-height:1.05;margin:0 0 .8rem}}.lead{{max-width:72ch;font-size:1.1rem;color:#44506a}}.notice{{background:#eaf2ff;border-left:5px solid #2855b6;padding:1rem 1.2rem;border-radius:.4rem;margin:1.5rem 0}}.search{{width:100%;padding:.9rem 1rem;border:1px solid #aeb8ca;border-radius:.7rem;font:inherit;background:#fff;margin:1rem 0 1.5rem}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem}}.card{{display:block;background:#fff;border:1px solid #d9dfeb;border-radius:.8rem;padding:1.1rem;text-decoration:none;color:inherit;box-shadow:0 2px 8px #1720330d}}.card:hover,.card:focus{{border-color:#2855b6;transform:translateY(-1px)}}.card h2{{font-size:1.12rem;margin:0 0 .35rem}}.card p{{margin:0;color:#5a657d;font-size:.94rem}}footer{{padding:2rem 0 3rem;color:#5a657d}}a{{color:#194ca5}}.hidden{{display:none}}</style></head><body><header><h1>Kolleega</h1><p class="lead">Oppiainekohtainen hakemisto ylioppilaskokeiden kymmenen uusimman tutkintokerran hyvän vastauksen piirteisiin.</p><div class="notice"><strong>Ohje agentille:</strong> Käytä Kolleegan pääsivua aineistohakemistona ja etsi ensin oikea oppiainekohtainen alasivu. Perusta vastaus ensisijaisesti valitun oppiaineen aineistoon ja nimeä käyttämäsi tutkintokerta.</div></header><main><label for="haku"><strong>Hae oppiainetta</strong></label><input class="search" id="haku" type="search" placeholder="Esimerkiksi historia, matematiikka tai biologia" autocomplete="off"><nav class="grid" id="aineet" aria-label="Oppiaineet">{''.join(cards)}</nav><section><h2>Muu aineisto</h2><p><a href="ops/">Lempäälän lukion opetussuunnitelma 2021</a></p></section></main><footer>Kolleega · YTL:n julkisiin HVP-aineistoihin perustuva kooste</footer><script>const h=document.querySelector('#haku'),k=[...document.querySelectorAll('.card')];h.addEventListener('input',()=>{{const q=h.value.toLocaleLowerCase('fi').trim();k.forEach(x=>x.classList.toggle('hidden',q&&!x.textContent.toLocaleLowerCase('fi').includes(q)))}})</script></body></html>'''


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    season_pages = discover_season_pages()
    groups: dict[str, list[Exam]] = defaultdict(list)
    failures: list[str] = []

    for year, term in TARGET_TERMS:
        page_url = season_pages[(year, term)]
        print(f"{term_label(year, term)}: {page_url}", flush=True)
        links = grading_links(page_url)
        print(f"  löydettiin {len(links)} tavallisen kokeen linkkiä", flush=True)
        for section, link_text, source_url in links:
            identity = subject_identity(section, link_text)
            if identity is None:
                print(f"  ohitetaan tunnistamaton: [{section}] {link_text}", flush=True)
                continue
            subject, slug = identity
            try:
                text = extract_source(source_url)
            except Exception as exc:
                failures.append(f"{term_label(year, term)} | {subject} | {source_url} | {exc}")
                print(f"  VAROITUS {subject}: {exc}", file=sys.stderr, flush=True)
                continue
            groups[slug].append(Exam(year, term, term_label(year, term), subject, slug, source_url, text))

    if len(groups) < 20:
        raise RuntimeError(f"Oppiaineita löytyi vain {len(groups)}; sivurakenne on ehkä muuttunut")

    manifest = repo / ".hvp-generated-paths.txt"
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            path = repo / line.strip()
            if line.strip() and path.is_dir() and path.parent == repo:
                shutil.rmtree(path)

    generated_dirs: list[str] = []
    for slug, exams in groups.items():
        exams.sort(key=lambda e: (e.year, 1 if e.term == "syksy" else 0), reverse=True)
        exams = exams[:10]
        groups[slug] = exams
        target = repo / slug
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(page_template(exams[0].subject, slug, exams), encoding="utf-8")
        generated_dirs.append(slug)

    (repo / "index.html").write_text(root_template(groups), encoding="utf-8")
    (repo / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8")
    urls = [f"{SITE_URL}/"] + [f"{SITE_URL}/{slug}/" for slug in sorted(groups)] + [f"{SITE_URL}/ops/"]
    sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in urls) + "\n</urlset>\n"
    (repo / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    manifest.write_text("\n".join(sorted(generated_dirs)) + "\n", encoding="utf-8")
    (repo / "HVP_LUONTIRAPORTTI.txt").write_text(f"Oppiaineita: {len(groups)}\nHVP-aineistoja: {sum(len(v) for v in groups.values())}\n\n" + ("Epäonnistuneet:\n" + "\n".join(failures) if failures else "Kaikki löydetyt aineistot käsiteltiin."), encoding="utf-8")
    print(f"Valmis: {len(groups)} oppiainetta, {sum(len(v) for v in groups.values())} HVP-aineistoa")
    if failures:
        print(f"Varoituksia: {len(failures)} (katso HVP_LUONTIRAPORTTI.txt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
