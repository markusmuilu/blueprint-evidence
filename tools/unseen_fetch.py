"""Download the public, openly licensed drawings of the unseen test set into data/unseen/<name>/.

Plain data only: nothing downloaded is executed or imported as code. Wikimedia licences and authors
are read from the Commons API at download time and written to SOURCE.md next to each file.
    python tools/unseen_fetch.py --out ../data/unseen
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "unseen-eval/0.1 (research; open data)"}

GITHUB = [
    # name, file, url, licence, note
    ("bsmart_building_arch_ifc4", "Building-Architecture.ifc",
     "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/main/IFC%204.0.2.1%20(IFC%204%20ADD2%20TC1)/Simple-Scene/Building-Architecture.ifc",
     "CC BY 4.0 (buildingSMART International Ltd.; repo LICENSE)", "https://github.com/buildingSMART/Sample-Test-Files"),
    ("bsmart_building_arch_ifc2x3", "Building-Architecture.ifc",
     "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/main/IFC%202.3.0.1%20(IFC%202x3%20TC1)/Simple-Scene/Building-Architecture.ifc",
     "CC BY 4.0 (buildingSMART International Ltd.; repo LICENSE)", "https://github.com/buildingSMART/Sample-Test-Files"),
    ("bsmart_wall_with_window", "wall-with-opening-and-window.ifc",
     "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/main/IFC%204.0.2.1%20(IFC%204%20ADD2%20TC1)/ISO%20Spec%20-%20ReferenceView_V1.2/wall-with-opening-and-window.ifc",
     "CC BY 4.0 (buildingSMART International Ltd.; repo LICENSE)", "https://github.com/buildingSMART/Sample-Test-Files"),
    ("ifcopenhouse_ifc4", "IfcOpenHouse_IFC4.ifc",
     "https://raw.githubusercontent.com/ThatOpen/engine_web-ifc/main/tests/ifcfiles/public/IfcOpenHouse_IFC4.ifc",
     "IfcOpenHouse example of IfcOpenShell (LGPL-3.0 project); file taken from the web-ifc test fixtures (MPL-2.0 repo)",
     "https://github.com/ThatOpen/engine_web-ifc/tree/main/tests/ifcfiles/public"),
    ("schependomlaan_design_ifc", "IFC Schependomlaan.ifc",
     "https://media.githubusercontent.com/media/buildingsmart-community/Community-Sample-Test-Files/main/IFC%202.3.0.1%20(IFC%202x3)/Schependomlaan/Design%20model%20IFC/IFC%20Schependomlaan.ifc",
     "CC BY 4.0 (community repo README: contributions are published under CC BY 4.0)",
     "https://github.com/buildingsmart-community/Community-Sample-Test-Files"),
    ("kjdraw_architecture_floor_plan", "architecture-floor-plan.dxf",
     "https://raw.githubusercontent.com/KanJieTeam/kjdraw/main/docs/latest/showcase/assets/architecture-floor-plan.dxf",
     "Apache-2.0 (KanJieTeam/kjdraw)", "https://github.com/KanJieTeam/kjdraw"),
    ("kjdraw_compact_office_plan", "compact-office-plan.dxf",
     "https://raw.githubusercontent.com/KanJieTeam/kjdraw/main/docs/latest/showcase/assets/compact-office-plan.dxf",
     "Apache-2.0 (KanJieTeam/kjdraw)", "https://github.com/KanJieTeam/kjdraw"),
    ("kjdraw_courtyard_plan", "courtyard-circulation-plan.dxf",
     "https://raw.githubusercontent.com/KanJieTeam/kjdraw/main/docs/latest/showcase/assets/courtyard-circulation-plan.dxf",
     "Apache-2.0 (KanJieTeam/kjdraw)", "https://github.com/KanJieTeam/kjdraw"),
]

COMMONS = [
    ("wm_747_villa", "747 villa Floor plan.pdf"),
    ("wm_gottlieb_ground", "Gottlieb House Ground Floor Plan.pdf"),
    ("wm_fraser_residence", "Fraser residence Floor Plan.pdf"),
    ("wm_milam_1st_floor", "Milam Residence 1st floor architectural plan.png"),
    ("wm_tyneside_flat", "Tyneside flat floorplan.png"),
    ("wm_ballymun_flat", "Typical floor plan of a Ballymun flat, drawn 1965.png"),
]


def get(url):
    for wait in (0, 10, 30, 60, 120):
        time.sleep(wait)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
    raise SystemExit("rate limited: " + url)


def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    for name, fn, url, lic, page in GITHUB:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        if (d / "SOURCE.md").exists():
            continue
        data = get(url)
        if data.startswith(b"version https://git-lfs"):
            raise SystemExit(f"{name}: got an LFS pointer, not the file")
        (d / fn).write_bytes(data)
        (d / "SOURCE.md").write_text(f"# {name}\n\n- File: `{fn}` ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()})\n"
                                     f"- URL: {url}\n- Project page: {page}\n- Licence: {lic}\n", encoding="utf-8")
        print("ok", name, len(data))
    for name, title in COMMONS:
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        if (d / "SOURCE.md").exists():
            continue
        q = urllib.parse.urlencode({"action": "query", "titles": "File:" + title, "prop": "imageinfo",
                                    "iiprop": "url|size|mime|extmetadata", "format": "json"})
        info = json.loads(get("https://commons.wikimedia.org/w/api.php?" + q))
        page = next(iter(info["query"]["pages"].values()))["imageinfo"][0]
        em = page["extmetadata"]
        data = get(page["url"])
        fn = title
        (d / fn).write_bytes(data)
        (d / "SOURCE.md").write_text(
            f"# {name}\n\n- File: `{fn}` ({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()}, {page['mime']}, {page['width']}x{page['height']})\n"
            f"- URL: {page['url'].split('?')[0]}\n- Commons page: https://commons.wikimedia.org/wiki/{urllib.parse.quote('File:' + title.replace(' ', '_'))}\n"
            f"- Licence: {em.get('LicenseShortName', {}).get('value')} ({em.get('LicenseUrl', {}).get('value', 'no url')})\n"
            f"- Author: {strip_html(em.get('Artist', {}).get('value'))}\n"
            f"- Credit: {strip_html(em.get('Credit', {}).get('value'))}\n", encoding="utf-8")
        print("ok", name, len(data), em.get("LicenseShortName", {}).get("value"))


if __name__ == "__main__":
    main()
