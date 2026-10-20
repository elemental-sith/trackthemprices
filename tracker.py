import os, time
"""Reads products.txt, checks each product page, and records the price in data/prices.json."""
import json, re, pathlib, datetime
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
DATA = pathlib.Path("data/prices.json")


def to_number(value):
    """Turns '1,299.00', '1.299,00' or 1299 into a float."""
    s = re.sub(r"[^\d.,]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) <= 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def walk(node):
    """Finds every 'Product' block inside the page's structured data."""
    if isinstance(node, list):
        for n in node:
            yield from walk(n)
    elif isinstance(node, dict):
        t = node.get("@type")
        if "Product" in (t if isinstance(t, list) else [t]):
            yield node
        for v in node.values():
            if isinstance(v, (dict, list)):
                yield from walk(v)


def read_offer(offers):
    if isinstance(offers, list):
        for o in offers:
            r = read_offer(o)
            if r:
                return r
    elif isinstance(offers, dict):
        spec = offers.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else (spec or {})
        price = to_number(offers.get("price") or offers.get("lowPrice") or spec.get("price") or "")
        cur = offers.get("priceCurrency") or spec.get("priceCurrency")
        if price:
            return price, cur
    return None


def shorten(name, words=5):
    name = re.split(r"\s[-|–—]\s|,|\(", name)[0].strip()
    return " ".join(name.split()[:words])


def scrape(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    name = price = cur = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for prod in walk(data):
            offer = read_offer(prod.get("offers"))
            if offer:
                name = name or prod.get("name")
                price, cur = offer
                break
        if price:
            break
    if not price:  # fallback: Open Graph / meta tags
        def meta(*keys):
            for k in keys:
                m = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k})
                if m and m.get("content"):
                    return m["content"]
        price = to_number(meta("product:price:amount", "og:price:amount") or "")
        cur = meta("product:price:currency", "og:price:currency")
    name = name or (soup.find("meta", property="og:title") or {}).get("content") \
        or (soup.title.string if soup.title else url)
    site = (soup.find("meta", property="og:site_name") or {}).get("content") \
        or urlparse(url).netloc.replace("www.", "")
    if not price:
        raise ValueError("price not found on page")
    return {"name": shorten(name), "store": site, "currency": cur or "?", "price": price}


def main():
    base = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
    key = os.environ["SUPABASE_SERVICE_KEY"]
    api = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    r = requests.get(base + "/products?select=id,url", headers=api, timeout=30)
    r.raise_for_status()
    for p in r.json():
        patch = base + "/products?id=eq." + p["id"]
        try:
            info = scrape(p["url"])
            requests.post(base + "/prices", headers=api, timeout=30,
                          json={"product_id": p["id"], "price": info["price"]}).raise_for_status()
            requests.patch(patch, headers=api, timeout=30,
                           json={"name": info["name"], "store": info["store"],
                                 "currency": info["currency"], "last_error": None}).raise_for_status()
            print("OK  ", p["url"], info["price"], info["currency"])
        except Exception as e:
            requests.patch(patch, headers=api, timeout=30, json={"last_error": str(e)[:120]})
            print("FAIL", p["url"], e)
        time.sleep(2)  # be polite to the shops


if __name__ == "__main__":
    main()
