import requests
import re
import json
import os
import datetime
import gzip
from bs4 import BeautifulSoup

GUILLORY_URL = 'https://www.guilloryoil.net'
GUILLORY_USERNAME = os.environ.get('GUILLORY_USERNAME', '')
GUILLORY_PASSWORD = os.environ.get('GUILLORY_PASSWORD', '')

GUILLORY_STORES = {
        '000002080': 'gw',
        '000002555': 'ge',
        '000005310': 'gr',
        '000005315': 'gc',
        '000004644': 'gh',
        '000005295': 'gb',
}

GUILLORY_PRODUCTS = {
        'REGULAR E10': 'reg',
        'REGULAR': 'reg_pure',
        'PLUS E10': 'mid',
        'SUPER E10': 'sup',
        'ULTRA L/S CLEAR DIESEL FUEL': 'die',
}

ALL_STORE_KEYS = list(GUILLORY_STORES.values())

def decode_html(response):
        """Decode response to HTML string, handling gzip if needed."""
        try:
                    content = response.content
                    if content[:2] == b'\x1f\x8b':
                                    return gzip.decompress(content).decode('utf-8', errors='replace')
                                return content.decode('utf-8', errors='replace')
except Exception:
        return response.text

def guillory_login():
        """Log in and return session (or None on failure)."""
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })

    # Step 1: GET login page for CSRF token
        login_url = GUILLORY_URL + '/account/?login'
        r1 = session.get(login_url)
        html1 = decode_html(r1)
        print('  Step1 GET login: status=' + str(r1.status_code) + ', len=' + str(len(html1)))

    soup1 = BeautifulSoup(html1, 'html.parser')
    csrf_input = soup1.find('input', {'name': 'csrf_token'})
    csrf_value = csrf_input['value'] if csrf_input else ''
    user_app_id_input = soup1.find('input', {'name': 'user_app_id'})
    user_app_id_value = user_app_id_input['value'] if user_app_id_input else '0'
    redirect_uri_input = soup1.find('input', {'name': 'redirect_uri'})
    redirect_uri_value = redirect_uri_input['value'] if redirect_uri_input else '/account/?login'

    print('  CSRF found: ' + str(bool(csrf_value)) + ', csrf_len=' + str(len(csrf_value)))
    print('  user_app_id: ' + str(user_app_id_value))
    print('  USERNAME set: ' + str(bool(GUILLORY_USERNAME)))

    # Step 2: POST login
    session.headers.update({'Referer': login_url, 'Origin': GUILLORY_URL})
    login_payload = {
                'user_app_id': user_app_id_value,
                'account_number': '',
                'csrf_token': csrf_value,
                'redirect_uri': redirect_uri_value,
                'user_name': GUILLORY_USERNAME,
                'user_password': GUILLORY_PASSWORD,
                'user_remember': '1',
    }
    r2 = session.post(GUILLORY_URL + '/account/', data=login_payload, allow_redirects=True)
    html2 = decode_html(r2)
    print('  Step2 POST login: status=' + str(r2.status_code) + ', final_url=' + str(r2.url) + ', len=' + str(len(html2)))

    has_logout = 'logout' in html2.lower()
    has_account_summary = 'Account Summary' in html2
    has_price_history = 'Price History' in html2 or 'price-history' in html2
    has_fuel_price = 'Fuel Price' in html2
    print('  has_logout=' + str(has_logout) + ', has_account_summary=' + str(has_account_summary) + ', has_price_history=' + str(has_price_history))

    soup2_dbg = BeautifulSoup(html2, 'html.parser')
    title_tag = soup2_dbg.find('title')
    print('  Response title: ' + (title_tag.get_text() if title_tag else 'none'))

    # Login succeeded if we see any authenticated-page indicators
    if has_logout or has_account_summary or has_price_history or has_fuel_price:
                print('  Login SUCCESS (len=' + str(len(html2)) + ')')
                return session

    # Login failed - print debug info
    err = (soup2_dbg.find(class_='alert-danger') or
                      soup2_dbg.find(class_='error') or
                      soup2_dbg.find(class_='alert'))
    err_text = err.get_text(strip=True)[:100] if err else 'no error msg found'
    print('  LOGIN FAILED. Error: ' + err_text)
    snippet = html2[:500].encode('ascii', errors='replace').decode()
    print('  RESP_SNIPPET: ' + snippet)
    resp_headers = dict(r2.headers)
    print('  RESP_HEADERS: ' + str({k: v for k, v in resp_headers.items() if k.lower() in ['content-type', 'set-cookie', 'location', 'x-blocked', 'cf-ray']}))
    return None

def fetch_guillory_price_history(session, account_number, days=90):
        """POST to price-history page for a specific account and return HTML."""
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days)
        date_start = start.strftime('%m/%d/%Y')
        date_end = today.strftime('%m/%d/%Y')
        payload = {
            'search': '1',
            'results': '9999',
            'user_id': '',
            'account_number': account_number,
            'date_start': date_start,
            'date_end': date_end,
            'location_number': '',
            'supplier_id': '',
            'terminal_id': '',
            'product_id': '',
        }
        r = session.post(
            GUILLORY_URL + '/account/price-history',
            data=payload,
            headers={'Referer': GUILLORY_URL + '/account/'},
        )
        html = decode_html(r)
        print('  POST price-history(' + account_number + '): status=' + str(r.status_code) + ', len=' + str(len(html)))

    # Check if we got the login page back
        if 'user_password' in html and 'logout' not in html.lower():
                    print('  Got login page - session lost!')
                    return ''

    return html

def parse_guillory_html(html):
        """Parse price-history HTML into {date_str: {prod_key: price}}."""
        if not html:
                    return {}

        # Primary: JS DataTables data array in script tag
        for pattern in [r"'data':\s*(\[\[.*?\]\])", r'"data":\s*(\[\[.*?\]\])']:
                    m = re.search(pattern, html, re.DOTALL)
                    if m:
                                    try:
                                                        data = json.loads(m.group(1))
                                                        result = _rows_to_result(data)
                                                        if result:
                                                                                n_dates = len(result)
                                                                                n_prods = sum(len(v) for v in result.values())
                                                                                print('  Parsed via JS data: ' + str(n_dates) + ' dates, ' + str(n_prods) + ' products')
                                                                                return result
                                    except Exception as e:
                                                        print('  JS data parse error: ' + str(e))

                            # Fallback: HTML table
                            soup = BeautifulSoup(html, 'html.parser')
    tables = soup.find_all('table')
    if tables:
                result = _tables_to_result(tables)
        if result:
                        return result

    print('  No data found. HTML length=' + str(len(html)))
    snippet = html[:200].encode('ascii', errors='replace').decode()
    print('  Snippet: ' + snippet)
    return {}

def _rows_to_result(data):
        """Convert DataTables row data to {date_str: {prod_key: price}}."""
    result = {}
    skipped = 0
    for row in data:
                try:
                                if len(row) < 10:
                                                    skipped += 1
                                                    continue
                                                date_str_raw = str(row[0]).replace('\/', '/')
            d = datetime.datetime.strptime(date_str_raw, '%m/%d/%Y').date()
            prod_key = GUILLORY_PRODUCTS.get(str(row[5]))
            if not prod_key:
                                skipped += 1
                continue
            date_str = d.isoformat()
            if date_str not in result:
                                result[date_str] = {}
            result[date_str][prod_key] = float(row[9])
except Exception:
            skipped += 1
    if skipped:
                print('  Skipped ' + str(skipped) + ' rows')
    return result

def _tables_to_result(tables):
        """Parse HTML tables into {date_str: {prod_key: price}}."""
    result = {}
    for table in tables:
                rows = table.find_all('tr')
        if len(rows) < 2:
                        continue
        headers = [th.get_text(strip=True).lower()
                                      for th in rows[0].find_all(['th', 'td'])]
        if len(headers) < 4:
                        continue
        date_idx = next((i for i, h in enumerate(headers) if 'date' in h), 0)
        prod_idx = next((i for i, h in enumerate(headers) if 'product' in h), 2)
        total_idx = next((i for i, h in enumerate(headers) if 'total' in h), 6)
        print('  Table: ' + str(len(rows) - 1) + ' rows, headers=' + str(headers[:6]))
        parsed = 0
        for row in rows[1:]:
                        cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if len(cells) <= max(date_idx, prod_idx, total_idx):
                                continue
            try:
                                d = datetime.datetime.strptime(cells[date_idx], '%m/%d/%Y').date()
                prod_key = GUILLORY_PRODUCTS.get(cells[prod_idx].upper())
                if not prod_key:
                                        continue
                                    total = float(cells[total_idx].replace('$', '').replace(',', '').strip())
                date_str = d.isoformat()
                if date_str not in result:
                                        result[date_str] = {}
                                    result[date_str][prod_key] = total
                parsed += 1
except Exception:
                pass
        if parsed > 0:
                        print('  Parsed ' + str(parsed) + ' records, ' + str(len(result)) + ' dates')
    return result

def fetch_all_guillory():
        """Login, fetch price history per store, and return combined data."""
    session = guillory_login()
    if not session:
                print('  Login failed - aborting')
        return {}

    all_data = {}
    for account_number, store_key in GUILLORY_STORES.items():
                print('  Fetching ' + store_key + ' (' + account_number + ')...')
        try:
                        html = fetch_guillory_price_history(session, account_number)
            prices = parse_guillory_html(html)
            print('  Got ' + str(len(prices)) + ' dates')
            for date_str, prods in prices.items():
                                if date_str not in all_data:
                                                        all_data[date_str] = {}
                                                    all_data[date_str][store_key] = prods
except Exception as e:
            print('  Error fetching ' + store_key + ': ' + str(e))

    return all_data

def update_guillory_in_prices_json(guillory_data):
        """Merge new Guillory prices into prices.json."""
    prices_file = 'prices.json'
    if os.path.exists(prices_file):
                with open(prices_file, 'r') as f:
                                existing = json.load(f)
else:
        existing = {}

    added = 0
    for date_str, store_data in guillory_data.items():
                if date_str not in existing:
                                existing[date_str] = {}
        for store_key, prods in store_data.items():
                        if store_key not in existing[date_str]:
                                            existing[date_str][store_key] = prods
                                            added += 1
                                            print('  Added ' + date_str + ' ' + store_key)

    with open(prices_file, 'w') as f:
                json.dump(existing, f, indent=2, sort_keys=True)
    print('Guillory: ' + str(added) + ' new entries added')
    return existing

def build_gd_js(prices_data):
        """Build var GD={...} JavaScript from prices_data."""
    guillory_keys = set(GUILLORY_STORES.values())
    lines = ['var GD={']
    guillory_dates = sorted(
                dk for dk in prices_data
                if any(sk in prices_data[dk] for sk in guillory_keys)
    )
    for i, dk in enumerate(guillory_dates):
                parts = []
        for sk in sorted(guillory_keys):
                        if sk in prices_data[dk]:
                                            pp = ['"' + k + '":' + str(v)
                                                                        for k, v in sorted(prices_data[dk][sk].items())]
                                            parts.append('"' + sk + '":{' + ','.join(pp) + '}')
                                    if parts:
                                                    comma = ',' if i < len(guillory_dates) - 1 else ''
                                                    lines.append('"' + dk + '":{' + ','.join(parts) + '}' + comma)
                                            lines.append('};')
    return '\n'.join(lines)

def update_gd_in_index_html(prices_data):
        """Replace var GD={...} in index.html with fresh data."""
    gd_js = build_gd_js(prices_data)
    with open('index.html', 'r', encoding='utf-8') as f:
                html = f.read()
    html = re.sub(r'var GD=\{[\s\S]*?\};', gd_js, html)
    with open('index.html', 'w', encoding='utf-8') as f:
                f.write(html)
    print('Updated var GD= in index.html')

def main():
        print('Starting Guillory Oil price fetch...')
    if not GUILLORY_USERNAME or not GUILLORY_PASSWORD:
                print('Missing credentials - skipping')
        return
    guillory_data = fetch_all_guillory()
    if not guillory_data:
                print('No Guillory data fetched - aborting')
        return
    prices_data = update_guillory_in_prices_json(guillory_data)
    update_gd_in_index_html(prices_data)
    print('Done!')

if __name__ == '__main__':
        main()
