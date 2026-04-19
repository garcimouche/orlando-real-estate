#!/usr/bin/env python3
"""
Flight finder for Montreal-area → Orlando (MCO) round trips.

Searches cheap round-trip fares from YUL, BTV, PBG, YOW via the
kiwi-com-cheap-flights RapidAPI wrapper. Uses date-range params to cover
+/- N day flex in a single API call per origin. Filters stops client-side
(the wrapper's stopsNumber param is ignored by the upstream) and multiplies
per-person prices by --pax for display.
"""

import argparse
import html
import json
import logging
import os
import sys
import webbrowser
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_ORIGINS = ['YUL', 'BTV', 'PBG', 'YOW']
DEFAULT_DESTINATION = 'MCO'

ORIGIN_LABELS = {
    'YUL': 'Montreal-Trudeau',
    'BTV': 'Burlington VT',
    'PBG': 'Plattsburgh NY',
    'YOW': 'Ottawa',
    'MCO': 'Orlando',
}


class KiwiFlightAPI:
    """Client for the kiwi-com-cheap-flights RapidAPI wrapper.

    Uses the /round-trip endpoint with IATA codes and date-range params for
    flexible dates. Always queries with adults=1 -- the wrapper requires
    adultsHoldBags/adultsHandBags as per-adult arrays for adults>=2 and that
    shape can't be expressed in a querystring. Per-person prices are
    multiplied by pax in the output layer.
    """

    HOST = 'kiwi-com-cheap-flights.p.rapidapi.com'
    ENDPOINT = '/round-trip'

    def __init__(self, api_key: str = None, local_mode: bool = False):
        self.api_key = api_key or os.getenv('REALTY_API_KEY')
        if not self.api_key:
            raise ValueError('REALTY_API_KEY not found in environment.')
        self.local_mode = local_mode
        self.base_url = f'https://{self.HOST}'
        self.headers = {
            'X-RapidAPI-Key': self.api_key,
            'X-RapidAPI-Host': self.HOST,
        }
        self.logger = logging.getLogger(__name__)
        self.api_call_count = 0

        cache_dir = os.path.join(
            os.path.dirname(__file__), '..', 'cache', 'flights'
        )
        os.makedirs(cache_dir, exist_ok=True)
        self._cache_dir = cache_dir
        self._cache_ttl_hours = 6

    def search_round_trip(
        self,
        origin: str,
        destination: str,
        out_start: date,
        out_end: date,
        ret_start: date,
        ret_end: date,
    ) -> List[Dict]:
        """Fetch round-trip itineraries for one origin / date-range pair.

        Returns the raw ``itineraries`` list from the API, empty on error.
        Uses disk cache (6h TTL) keyed on origin+destination+date ranges.
        In local_mode, only cached results are returned.
        """
        cache_key = (
            f"{origin}_{destination}_"
            f"{out_start.isoformat()}_{out_end.isoformat()}_"
            f"{ret_start.isoformat()}_{ret_end.isoformat()}"
        )
        cache_path = os.path.join(self._cache_dir, f"{cache_key}.json")

        cached = self._load_cached(cache_path)
        if cached is not None:
            self.logger.info(f"[cache] {origin}: {len(cached)} itineraries")
            return cached

        if self.local_mode:
            self.logger.warning(
                f"[local] no cached data for {origin} {out_start}..{out_end} / "
                f"{ret_start}..{ret_end}"
            )
            return []

        params = {
            'source': origin,
            'destination': destination,
            'currency': 'cad',
            'locale': 'en',
            'adults': '1',
            'cabinClass': 'ECONOMY',
            'sortBy': 'PRICE',
            'sortOrder': 'ASCENDING',
            'outboundDepartureDateStart': f'{out_start.isoformat()}T00:00:00',
            'outboundDepartureDateEnd':   f'{out_end.isoformat()}T23:59:59',
            'inboundDepartureDateStart':  f'{ret_start.isoformat()}T00:00:00',
            'inboundDepartureDateEnd':    f'{ret_end.isoformat()}T23:59:59',
        }

        url = f"{self.base_url}{self.ENDPOINT}"
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                self.api_call_count += 1
                resp = requests.get(url, headers=self.headers, params=params, timeout=25)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('__typename') != 'Itineraries':
                        self.logger.error(
                            f"Wrapper error for {origin}: {data.get('error') or data}"
                        )
                        return []
                    itineraries = data.get('itineraries') or []
                    prov_status = (data.get('metadata', {}) or {}).get('statusPerProvider', [])
                    for s in prov_status:
                        if s.get('errorHappened'):
                            self.logger.warning(
                                f"Provider warning for {origin}: {s.get('errorMessage')}"
                            )
                    self._save_cache(cache_path, itineraries)
                    self.logger.info(f"{origin}: {len(itineraries)} itineraries")
                    return itineraries
                elif resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    self.logger.warning(
                        f"HTTP {resp.status_code} for {origin}, retry in {wait}s"
                    )
                    import time; time.sleep(wait)
                    continue
                else:
                    self.logger.error(
                        f"HTTP {resp.status_code} for {origin}: {resp.text[:200]}"
                    )
                    return []
            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    self.logger.warning(f"Timeout for {origin}, retrying...")
                    import time; time.sleep(2 ** (attempt + 1))
                    continue
                self.logger.error(f"Timeout for {origin} after retries")
                return []
            except Exception as e:
                self.logger.error(f"Error fetching {origin}: {e}")
                return []
        return []

    # ------------------------------------------------------------------
    # Cache helpers
    # ------------------------------------------------------------------

    def _load_cached(self, path: str) -> Optional[List[Dict]]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                entry = json.load(f)
            cached_at = datetime.fromisoformat(entry['_cached_at'])
            age_hours = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
            if age_hours > self._cache_ttl_hours and not self.local_mode:
                return None
            return entry.get('itineraries') or []
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            self.logger.warning(f"Could not read cache {path}: {e}")
            return None

    def _save_cache(self, path: str, itineraries: List[Dict]):
        try:
            with open(path, 'w') as f:
                json.dump({
                    '_cached_at': datetime.now(timezone.utc).isoformat(),
                    'itineraries': itineraries,
                }, f)
        except OSError as e:
            self.logger.warning(f"Could not save cache {path}: {e}")


def parse_itinerary(origin: str, it: Dict) -> Optional[Dict]:
    """Flatten a raw Kiwi itinerary into the dict used by the output layer.

    Returns None if the itinerary is malformed.
    """
    try:
        price_pp = float(it['price']['amount'])
        out_segs = it['outbound']['sectorSegments']
        in_segs = it['inbound']['sectorSegments']
        if not out_segs or not in_segs:
            return None

        def leg_summary(segs):
            first = segs[0]['segment']
            last = segs[-1]['segment']
            carriers = []
            for s in segs:
                code = (s['segment'].get('carrier') or {}).get('code')
                if code and code not in carriers:
                    carriers.append(code)
            total_duration_s = sum(s['segment'].get('duration') or 0 for s in segs)
            return {
                'depart_local': first['source']['localTime'],
                'arrive_local': last['destination']['localTime'],
                'from': first['source']['station']['code'],
                'to': last['destination']['station']['code'],
                'stops': len(segs) - 1,
                'carriers': carriers,
                'duration_min': total_duration_s // 60,
            }

        booking_path = None
        edges = (it.get('bookingOptions') or {}).get('edges') or []
        if edges:
            booking_path = ((edges[0] or {}).get('node') or {}).get('bookingUrl')
        booking_url = (
            f"https://www.kiwi.com{booking_path}" if booking_path else None
        )

        return {
            'origin': origin,
            'price_per_person_cad': price_pp,
            'virtual_interline': bool(
                (it.get('travelHack') or {}).get('isVirtualInterlining')
            ),
            'outbound': leg_summary(out_segs),
            'inbound': leg_summary(in_segs),
            'booking_url': booking_url,
            'kiwi_id': it.get('id'),
        }
    except (KeyError, TypeError, ValueError):
        return None


def parse_iso_date(value: str) -> date:
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise SystemExit(f"Invalid date '{value}' -- expected YYYY-MM-DD")


class _HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Shows defaults in help strings and preserves newlines in the epilog."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='find_flights.py',
        description=(
            'Find cheap round-trip flights to Orlando (MCO) from Montreal-area '
            'airports. Results are filtered to max N stops per leg and sorted '
            'by total CAD price for the requested number of passengers.'
        ),
        formatter_class=_HelpFormatter,
        epilog=(
            'Examples:\n'
            '  python3 find_flights.py --from 2026-11-05 --to 2026-11-14\n'
            '  python3 find_flights.py --from 2026-11-05 --to 2026-11-14 --pax 3\n'
            '  python3 find_flights.py --from 2026-11-05 --to 2026-11-14 --max-stops 0\n'
            '  python3 find_flights.py --from 2026-11-05 --to 2026-11-14 --local\n'
        ),
    )
    parser.add_argument(
        '--from', dest='depart', required=True, metavar='YYYY-MM-DD',
        help='Outbound departure date (ISO format).',
    )
    parser.add_argument(
        '--to', dest='return_date', required=True, metavar='YYYY-MM-DD',
        help='Return departure date (ISO format).',
    )
    parser.add_argument(
        '--pax', type=int, default=2, metavar='N',
        help='Number of passengers. Per-person price is multiplied by this.',
    )
    parser.add_argument(
        '--flex', type=int, default=1, metavar='DAYS',
        help='Flex +/- N days around each date (0 disables flex).',
    )
    parser.add_argument(
        '--no-flex', action='store_true',
        help='Shortcut for --flex 0 (strict dates only).',
    )
    parser.add_argument(
        '--max-stops', type=int, default=1, metavar='N',
        help='Max connections per leg (0 = direct only, 1 = one connection).',
    )
    parser.add_argument(
        '--origins', default=','.join(DEFAULT_ORIGINS), metavar='CSV',
        help='Comma-separated IATA codes of origin airports.',
    )
    parser.add_argument(
        '--dest', default=DEFAULT_DESTINATION, metavar='IATA',
        help='Destination IATA code.',
    )
    parser.add_argument(
        '--limit', type=int, default=20, metavar='N',
        help='Max itineraries to print in the result table.',
    )
    parser.add_argument(
        '--local', action='store_true',
        help='Replay from cache only -- no API calls. Requires a prior live run.',
    )
    parser.add_argument(
        '--open', dest='open_html', action='store_true',
        help='Open the exported HTML page in the default browser after running.',
    )
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Enable DEBUG-level logging.',
    )

    args = parser.parse_args()
    if args.no_flex:
        args.flex = 0
    if args.flex < 0:
        parser.error('--flex must be >= 0')
    if args.pax < 1:
        parser.error('--pax must be >= 1')
    if args.max_stops < 0:
        parser.error('--max-stops must be >= 0')
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    logger = logging.getLogger(__name__)

    depart = parse_iso_date(args.depart)
    ret = parse_iso_date(args.return_date)
    if ret < depart:
        raise SystemExit('--to must be on or after --from')

    origins = [o.strip().upper() for o in args.origins.split(',') if o.strip()]
    destination = args.dest.strip().upper()

    flex = timedelta(days=args.flex)
    out_start, out_end = depart - flex, depart + flex
    ret_start, ret_end = ret - flex, ret + flex

    mode = '[LOCAL -- no API calls]' if args.local else ''
    logger.info(
        f"Searching {destination} from {origins} | "
        f"{depart} -> {ret} (+/-{args.flex}d) | "
        f"{args.pax} pax | max {args.max_stops} stop(s) {mode}".rstrip()
    )

    api = KiwiFlightAPI(local_mode=args.local)

    offers: List[Dict] = []
    for origin in origins:
        raw = api.search_round_trip(
            origin, destination,
            out_start, out_end, ret_start, ret_end,
        )
        for it in raw:
            parsed = parse_itinerary(origin, it)
            if parsed is None:
                continue
            if parsed['outbound']['stops'] > args.max_stops:
                continue
            if parsed['inbound']['stops'] > args.max_stops:
                continue
            parsed['total_cad'] = round(parsed['price_per_person_cad'] * args.pax, 2)
            offers.append(parsed)

    # Dedupe across origins: same dates + same carriers + same price.
    seen = set()
    unique: List[Dict] = []
    for o in offers:
        key = (
            o['origin'],
            o['outbound']['depart_local'],
            o['inbound']['depart_local'],
            tuple(o['outbound']['carriers']),
            tuple(o['inbound']['carriers']),
            round(o['price_per_person_cad'], 2),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(o)

    unique.sort(key=lambda x: x['total_cad'])

    logger.info(
        f"Collected {len(offers)} offers after filter, {len(unique)} unique. "
        f"API calls: {api.api_call_count}"
    )

    print_results(unique, args, depart, ret, destination)
    json_path = export_results(unique, args, depart, ret, destination)
    html_path = export_html(unique, args, depart, ret, destination)
    print(f"\nExported {len(unique)} offers:")
    print(f"  JSON : {json_path}")
    print(f"  HTML : {html_path}")
    if args.open_html:
        webbrowser.open(f'file://{os.path.abspath(html_path)}')
    return 0


def _fmt_hhmm(iso_local: str) -> str:
    return iso_local[11:16] if len(iso_local) >= 16 else iso_local


def _fmt_dur(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}"


def print_results(offers: List[Dict], args, depart: date, ret: date, destination: str):
    """Aligned table output. VI offers are flagged with a self-transfer note."""
    mode = ' [LOCAL]' if args.local else ''
    print()
    print('=' * 112)
    print(
        f"Vols MTL -> {destination}  |  {depart} -> {ret}  "
        f"(+/-{args.flex}d, max {args.max_stops} escale/vol, {args.pax} pax){mode}"
    )
    print('=' * 112)
    if not offers:
        print('Aucun vol ne correspond aux criteres.')
        return

    header = (
        f"{'#':>3}  {'Origin':<7} {'Total CAD':>10} {'/pers':>7}  "
        f"{'Depart (out)':<16} {'Ret (in)':<16}  "
        f"{'Stops':<5} {'Durees':<12}  {'Trans.':<12}"
    )
    print(header)
    print('-' * len(header))
    for i, o in enumerate(offers[:args.limit], 1):
        out, inb = o['outbound'], o['inbound']
        vi = ' * transfert auto.' if o['virtual_interline'] else ''
        dates = (
            f"{out['depart_local'][:10]} {_fmt_hhmm(out['depart_local'])}",
            f"{inb['depart_local'][:10]} {_fmt_hhmm(inb['depart_local'])}",
        )
        stops = f"{out['stops']}/{inb['stops']}"
        durs = f"{_fmt_dur(out['duration_min'])}/{_fmt_dur(inb['duration_min'])}"
        carriers = f"{'+'.join(out['carriers'])}/{'+'.join(inb['carriers'])}"
        print(
            f"{i:>3}  {o['origin']:<7} "
            f"${o['total_cad']:>9,.0f} ${o['price_per_person_cad']:>5,.0f}  "
            f"{dates[0]:<16} {dates[1]:<16}  "
            f"{stops:<5} {durs:<12}  {carriers:<12}{vi}"
        )
    print('-' * len(header))
    print(
        f"Prix: per-person * {args.pax} pax (taxes reelles peuvent varier a la reservation). "
        f"* = transfert auto-gere (vous recuperez/recheckez vos bagages)."
    )


def export_results(offers: List[Dict], args, depart: date, ret: date,
                   destination: str) -> str:
    """Write the full offer list to cache/flights/latest.json for future tooling."""
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'query': {
            'origins': [o.strip().upper() for o in args.origins.split(',') if o.strip()],
            'destination': destination,
            'depart': depart.isoformat(),
            'return': ret.isoformat(),
            'flex_days': args.flex,
            'max_stops_per_leg': args.max_stops,
            'pax': args.pax,
            'currency': 'CAD',
            'cabin': 'ECONOMY',
        },
        'count': len(offers),
        'offers': offers,
    }
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'cache', 'flights')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'latest.json')
    with open(path, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    return path


def export_html(offers: List[Dict], args, depart: date, ret: date,
                destination: str) -> str:
    """Render offers to cache/flights/latest.html -- compact, browsable, clickable."""
    rows = []
    for i, o in enumerate(offers, 1):
        out, inb = o['outbound'], o['inbound']
        vi_badge = '<span class="vi" title="Transfert auto-gere">VI</span>' if o['virtual_interline'] else ''
        url = html.escape(o.get('booking_url') or '', quote=True)
        book = (
            f'<a class="book" href="{url}" target="_blank" rel="noopener">Reserver</a>'
            if url else '<span class="nolink">-</span>'
        )
        rows.append(
            '<tr>'
            f'<td class="num">{i}</td>'
            f'<td class="orig">{html.escape(o["origin"])}</td>'
            f'<td class="price">${o["total_cad"]:,.0f}</td>'
            f'<td class="pers">${o["price_per_person_cad"]:,.0f}</td>'
            f'<td class="when">{html.escape(out["depart_local"][:10])} <span class="hm">{_fmt_hhmm(out["depart_local"])}</span></td>'
            f'<td class="when">{html.escape(inb["depart_local"][:10])} <span class="hm">{_fmt_hhmm(inb["depart_local"])}</span></td>'
            f'<td class="stops">{out["stops"]}/{inb["stops"]}</td>'
            f'<td class="dur">{_fmt_dur(out["duration_min"])} / {_fmt_dur(inb["duration_min"])}</td>'
            f'<td class="car">{html.escape("+".join(out["carriers"]))} / {html.escape("+".join(inb["carriers"]))}</td>'
            f'<td class="flag">{vi_badge}</td>'
            f'<td class="act">{book}</td>'
            '</tr>'
        )
    origins = ', '.join(o.strip().upper() for o in args.origins.split(',') if o.strip())
    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    title = f'Vols {origins} -> {destination} | {depart} -> {ret}'
    subtitle = (
        f'+/-{args.flex}j flex, max {args.max_stops} escale/vol, {args.pax} pax, CAD. '
        f'{len(offers)} offres. Genere {generated}.'
    )
    doc = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 14px/1.4 -apple-system, system-ui, sans-serif; margin: 24px; max-width: 1200px; }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  .sub {{ color: #666; font-size: 12px; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }}
  th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #e5e5e5; }}
  th {{ font-weight: 600; font-size: 12px; text-transform: uppercase; color: #666; background: #fafafa; position: sticky; top: 0; }}
  tr:hover td {{ background: #f5f8ff; }}
  .num, .stops {{ text-align: right; color: #999; }}
  .price {{ text-align: right; font-weight: 600; }}
  .pers {{ text-align: right; color: #666; }}
  .hm {{ color: #888; font-size: 12px; }}
  .dur, .car {{ font-size: 12px; color: #555; }}
  .vi {{ background: #fff3cd; color: #856404; font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: 600; }}
  .nolink {{ color: #ccc; }}
  a.book {{ display: inline-block; padding: 4px 10px; background: #0366d6; color: #fff; border-radius: 4px; text-decoration: none; font-size: 12px; font-weight: 600; }}
  a.book:hover {{ background: #0256b3; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1a1a1a; color: #e5e5e5; }}
    th {{ background: #2a2a2a; color: #aaa; }}
    th, td {{ border-color: #333; }}
    tr:hover td {{ background: #222; }}
    .hm, .sub {{ color: #888; }}
    .dur, .car, .pers {{ color: #aaa; }}
    .vi {{ background: #3a2f00; color: #ffc107; }}
  }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <div class="sub">{html.escape(subtitle)}</div>
  <table>
    <thead>
      <tr>
        <th>#</th><th>Origine</th><th>Total CAD</th><th>/pers</th>
        <th>Depart (aller)</th><th>Depart (retour)</th>
        <th>Escales</th><th>Durees</th><th>Transporteurs</th>
        <th>Flag</th><th></th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    out_dir = os.path.join(os.path.dirname(__file__), '..', 'cache', 'flights')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, 'latest.html')
    with open(path, 'w') as f:
        f.write(doc)
    return path


if __name__ == '__main__':
    sys.exit(main())
