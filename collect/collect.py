#!/usr/bin/env python3
"""
collect.py — pull business phone numbers for an area and write an import file
that "My Lists" accepts in Bulk Add (TXT or CSV).

Two sources:

  --source osm     OpenStreetMap (Overpass). Free, no key, legal. Phone coverage
                   is good in big cities (Jakarta/Bandung/Surabaya), close to
                   none in small towns.

  --source google  Google Places API (New). Every listed business, phone numbers
                   for almost all of them. Needs, once, in the Google Cloud
                   project that owns your key:
                     1) APIs & Services -> Library -> "Places API (New)" -> Enable
                     2) Billing attached to the project
                   Free monthly allowance covers a few thousand lookups; after
                   that Text Search is roughly $32 / 1000 requests.

Examples:
  python3 collect.py --source osm --area "Jakarta Pusat" --category restaurant,cafe \
      --out out/jakarta-restaurants

  python3 collect.py --source google --key AIza... --area "Cianjur, Jawa Barat" \
      --category restaurant --out out/cianjur-restaurants

Output (per --out prefix):
  <out>.csv   list,name,address,phone,source      (for Excel / archive)
  <out>.txt   one phone number per line           (drag-and-drop into Bulk Add)
"""
import argparse, csv, json, re, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "my-lists-collector/1.0 (github.com/yamannewtab2-max/my-lists)"}
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PLACES = "https://places.googleapis.com/v1/places:searchText"

# ---- same normalisation as the web app: 0812 / +62 812 / 62812 = one number ----
def canon(raw):
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    plus = s.startswith("+") or " 00" in s
    d = re.sub(r"\D", "", s)
    if not d:
        return None
    if len(d) > 20:
        m = re.search(r"(?:62|0|8)\d{7,13}", d)
        if not m:
            return None
        d = m.group(0)
    if not re.match(r"^(?:62|0|8|00)", d):
        m = re.search(r"(?:62|0|8)\d{7,13}", d)
        if m:
            d = m.group(0)
        elif not (plus and re.match(r"^\d{8,15}$", d)):
            return None
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("620"):
        d = "62" + d[3:]
    if not d.startswith("62"):
        if d.startswith("0"):
            d = "62" + d[1:]
        elif d.startswith("8"):
            d = "62" + d
    if d.startswith("62"):
        if not re.match(r"^8\d{7,12}$", d[2:]):
            return None            # landline / not a mobile → cannot use WhatsApp
    elif not re.match(r"^\d{8,15}$", d):
        return None
    if re.match(r"^(\d)\1+$", d):
        return None
    return d


def http_json(url, data=None, headers=None, method=None, tries=3, form=False):
    body = None
    hdrs = dict(UA)
    if data is not None:
        if form:                                   # Overpass wants data=<urlencoded>, not JSON
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:                     # Overpass 504s and drops a lot
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"{url} failed after {tries} tries: {last}")


# ---------------- OpenStreetMap ----------------
def geocode(area):
    q = urllib.parse.urlencode({"q": area, "format": "json", "limit": 1})
    d = http_json(f"{NOMINATIM}?{q}")
    if not d:
        sys.exit(f"Could not find area: {area}")
    bbox = [float(x) for x in d[0]["boundingbox"]]     # S, N, W, E
    return d[0]["display_name"], f"{bbox[0]},{bbox[2]},{bbox[1]},{bbox[3]}"


def osm_rows(area, categories, limit):
    label, bbox = geocode(area)
    print(f"area: {label}\nbbox: {bbox}")
    rx = "|".join(categories)
    filters = [
        f'node["amenity"~"^({rx})$"]',
        f'way["amenity"~"^({rx})$"]',
        f'node["shop"~"^({rx})$"]',
        f'way["shop"~"^({rx})$"]',
        f'node["tourism"~"^({rx})$"]',
        f'way["tourism"~"^({rx})$"]',
    ]
    q = "[out:json][timeout:180];(" + "".join(
        f'{f}["phone"]({bbox});{f}["contact:phone"]({bbox});' for f in filters) + ");out center tags;"
    d = http_json(OVERPASS, {"data": q}, method="POST", form=True)
    out = []
    for e in d.get("elements", []):
        t = e.get("tags", {})
        phone = t.get("phone") or t.get("contact:phone") or t.get("contact:mobile") or ""
        name = t.get("name", "")
        addr = ", ".join(x for x in [t.get("addr:street"), t.get("addr:city")] if x)
        for piece in re.split(r"[;,/]", phone):
            out.append({"name": name, "address": addr, "phone": piece.strip(), "source": "openstreetmap"})
        if len(out) >= limit * 2:
            break
    return out


# ---------------- Google Places (New) ----------------
def google_rows(area, categories, limit, key, pages=3):
    out = []
    for cat in categories:
        token = None
        for _ in range(pages):
            body = {"textQuery": f"{cat} in {area}", "maxResultCount": 20}
            if token:
                body["pageToken"] = token
            d = http_json(PLACES, body, headers={
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "places.displayName,places.formattedAddress,"
                                     "places.nationalPhoneNumber,places.internationalPhoneNumber,"
                                     "places.websiteUri,nextPageToken",
            })
            for p in d.get("places", []):
                name = (p.get("displayName") or {}).get("text", "")
                phone = p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or ""
                out.append({"name": name, "address": p.get("formattedAddress", ""),
                            "phone": phone, "source": "google_places"})
                if len(out) >= limit * 2:
                    return out
            token = d.get("nextPageToken")
            if not token:
                break
            time.sleep(1.2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["osm", "google"], required=True)
    ap.add_argument("--area", required=True, help='e.g. "Cianjur, Jawa Barat"')
    ap.add_argument("--category", default="restaurant",
                    help="comma list: restaurant,cafe,hotel,guest_house,beauty,laundry,...")
    ap.add_argument("--out", required=True, help="output path prefix (no extension)")
    ap.add_argument("--key", help="Google Places API key (needed for --source google, or GOOGLE_KEY env)")
    ap.add_argument("--list-name", default="Maps", help="list label written into the CSV")
    ap.add_argument("--limit", type=int, default=2000)
    a = ap.parse_args()

    cats = [c.strip() for c in a.category.split(",") if c.strip()]
    rows = (osm_rows(a.area, cats, a.limit) if a.source == "osm"
            else google_rows(a.area, cats, a.limit, a.key or __import__("os").environ.get("GOOGLE_KEY", "")))

    seen, kept, skipped = set(), [], {"no_phone": 0, "invalid": 0, "duplicate": 0}
    for r in rows:
        n = canon(r["phone"])
        if not r["phone"].strip():
            skipped["no_phone"] += 1
            continue
        if not n:
            skipped["invalid"] += 1
            continue
        if n in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(n)
        kept.append({**r, "canonical": n})

    with open(a.out + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["list", "name", "address", "phone", "source"])
        for r in kept[:a.limit]:
            w.writerow([a.list_name, r["name"], r["address"], "+" + r["canonical"], r["source"]])
    with open(a.out + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join("+" + r["canonical"] for r in kept[:a.limit]))

    print(f"\nbusinesses scanned : {len(rows)}")
    print(f"unique numbers kept: {len(kept[:a.limit])}")
    print(f"skipped            : duplicates {skipped['duplicate']}, "
          f"invalid {skipped['invalid']}, no phone {skipped['no_phone']}")
    print(f"written            : {a.out}.csv  and  {a.out}.txt")
    print(f"\nMy Lists -> open a list -> Bulk Add -> choose {a.out}.txt")


if __name__ == "__main__":
    main()
