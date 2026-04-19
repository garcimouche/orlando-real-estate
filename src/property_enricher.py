#!/usr/bin/env python3
"""
Property Detail Enricher for Orlando STR Investments

Fetches detailed property data (HOA fees, tax info, year built, full description,
etc.) from the Realty-in-US API v2 detail endpoint to enrich properties found
by the list search.

Only enriches top-scoring candidates to minimize API calls.
"""

import requests
import time
import re
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from dotenv import load_dotenv
import os

load_dotenv()


class PropertyEnricher:
    """Fetches and parses property detail data from the API detail endpoint."""

    def __init__(self, api_key: str = None, local_mode: bool = False):
        self.api_key = api_key or os.getenv('DETAIL_API_KEY') or os.getenv('REALTY_API_KEY')
        if not self.api_key:
            raise ValueError("REALTY_API_KEY not found in environment.")

        # When local_mode=True, no API calls are made — only cached data is used.
        self.local_mode = local_mode

        # Counts actual HTTP calls made (cache hits excluded).
        self.api_call_count = 0

        # The v2/detail endpoint returns 204. The v3/detail on the same host works.
        # Override via DETAIL_API_HOST / DETAIL_API_ENDPOINT in .env if needed.
        self.detail_host = os.getenv(
            'DETAIL_API_HOST', 'realty-in-us.p.rapidapi.com'
        )
        self.detail_endpoint = os.getenv(
            'DETAIL_API_ENDPOINT', '/properties/v3/detail'
        )
        self.base_url = f"https://{self.detail_host}"
        self.headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.detail_host,
        }
        self.logger = logging.getLogger(__name__)

        # Persistent cache: stored as JSON on disk, loaded at init
        cache_dir = os.path.join(os.path.dirname(__file__), '..', 'cache')
        os.makedirs(cache_dir, exist_ok=True)
        self._cache_path = os.path.join(cache_dir, 'property_details.json')
        self._cache_ttl_days = int(os.getenv('DETAIL_CACHE_TTL_DAYS', 7))
        # Number of days a property stays "NEW!" after first being seen
        self._new_window_days = int(os.getenv('NEW_BADGE_WINDOW_DAYS', 7))
        self._cache: Dict[str, Dict] = self._load_cache()

        # Remember which IDs were in cache at startup so we can flag new ones.
        self._initial_cache_keys: set = set(self._cache.keys())

    # ------------------------------------------------------------------
    # Persistent cache
    # ------------------------------------------------------------------

    def _load_cache(self) -> Dict[str, Dict]:
        """Load cache from disk, discarding entries older than TTL."""
        if not os.path.exists(self._cache_path):
            return {}
        try:
            with open(self._cache_path, 'r') as f:
                raw = json.load(f)
            # Expire stale entries
            now = datetime.now(timezone.utc)
            valid = {}
            for pid, entry in raw.items():
                cached_at = entry.get('_cached_at')
                if cached_at:
                    age = (now - datetime.fromisoformat(cached_at)).days
                    if age > self._cache_ttl_days:
                        continue
                # Backfill _first_seen_at for legacy entries (pre-tracking)
                if not entry.get('_first_seen_at'):
                    entry['_first_seen_at'] = cached_at or now.isoformat()
                valid[pid] = entry
            if len(valid) < len(raw):
                self.logger.info(
                    f"Cache: kept {len(valid)}/{len(raw)} entries "
                    f"(expired {len(raw) - len(valid)} older than {self._cache_ttl_days} days)"
                )
            return valid
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Could not load cache, starting fresh: {e}")
            return {}

    def _is_recently_seen(self, first_seen_iso: Optional[str]) -> bool:
        """True if the property was first seen within the 'NEW!' window."""
        if not first_seen_iso:
            return True  # no record → treat as new
        try:
            first_seen = datetime.fromisoformat(first_seen_iso)
            age_days = (datetime.now(timezone.utc) - first_seen).days
            return age_days <= self._new_window_days
        except (ValueError, TypeError):
            return False

    def _save_cache(self):
        """Persist current cache to disk."""
        try:
            with open(self._cache_path, 'w') as f:
                json.dump(self._cache, f, default=str)
        except OSError as e:
            self.logger.warning(f"Could not save cache: {e}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enrich_properties(self, properties: List[Dict],
                          limit: int = 10,
                          delay: float = 1.0) -> List[Dict]:
        """Enrich a list of properties with detail data.

        Args:
            properties: Scored property dicts from the discovery agent.
            limit: Max number of properties to enrich (top N by score).
            delay: Seconds to wait between API calls.

        Returns:
            The same list with enrichment fields merged in.
        """
        # Only enrich top candidates to conserve API quota
        to_enrich = properties[:limit]
        self.logger.info(f"Enriching top {len(to_enrich)} properties with detail data")

        enriched = []
        for i, prop in enumerate(to_enrich):
            property_id = self._extract_property_id(prop)
            if not property_id:
                self.logger.warning(f"No property_id for {prop.get('address', '?')}, skipping")
                enriched.append(prop)
                continue

            detail = self._fetch_detail(property_id)
            if detail:
                prop = self._merge_detail(prop, detail)
            # Mark as new if first seen within the configured window
            first_seen = None
            if property_id in self._cache:
                first_seen = self._cache[property_id].get('_first_seen_at')
            prop['first_seen_at'] = first_seen
            prop['detail_is_new'] = self._is_recently_seen(first_seen)
            enriched.append(prop)

            # Rate-limit between calls
            if i < len(to_enrich) - 1:
                time.sleep(delay)

        # Persist cache to disk after enrichment round
        self._save_cache()

        # Append any remaining properties that were not enriched
        enriched.extend(properties[limit:])
        return enriched

    # ------------------------------------------------------------------
    # API call
    # ------------------------------------------------------------------

    def _fetch_detail(self, property_id: str) -> Optional[Dict]:
        """Fetch property detail from the API with retry logic."""
        if property_id in self._cache:
            self.logger.debug(f"Cache hit for {property_id}")
            return self._cache[property_id]

        # In local mode we never hit the API — return None if not cached.
        if self.local_mode:
            self.logger.debug(f"Local mode: no cached data for {property_id}, skipping")
            return None

        url = f"{self.base_url}{self.detail_endpoint}"
        params = {"property_id": property_id}

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                self.api_call_count += 1
                response = requests.get(
                    url, headers=self.headers, params=params, timeout=15
                )

                if response.status_code == 200:
                    data = response.json()
                    now_iso = datetime.now(timezone.utc).isoformat()
                    data['_cached_at'] = now_iso
                    # Preserve first_seen_at across re-fetches; stamp only once
                    prior = self._cache.get(property_id) or {}
                    data['_first_seen_at'] = prior.get('_first_seen_at') or now_iso
                    self._cache[property_id] = data
                    return data
                elif response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 2 ** (attempt + 1)
                    self.logger.warning(
                        f"Detail API returned {response.status_code} for {property_id}, "
                        f"retrying in {wait}s..."
                    )
                    time.sleep(wait)
                    continue
                else:
                    self.logger.error(
                        f"Detail API error {response.status_code} for {property_id}: "
                        f"{response.text[:200]}"
                    )
                    return None

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    self.logger.warning(f"Detail request timed out for {property_id}, retrying...")
                    time.sleep(2 ** (attempt + 1))
                    continue
                self.logger.error(f"Detail request timed out after all retries for {property_id}")
                return None
            except Exception as e:
                self.logger.error(f"Error fetching detail for {property_id}: {e}")
                return None

        return None

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _extract_property_id(self, prop: Dict) -> Optional[str]:
        """Extract the numeric property_id from our formatted property dict."""
        raw_id = prop.get('id', '')
        # Our id format is "realtor_{property_id}_{listing_id}"
        parts = raw_id.split('_')
        if len(parts) >= 2:
            return parts[1]
        return None

    def _merge_detail(self, prop: Dict, detail_response: Dict) -> Dict:
        """Merge enrichment fields from the detail response into the property dict."""
        # The v2 detail response structure varies, but typically:
        #   detail_response -> properties -> [0] (or detail_response -> data -> ...)
        detail = self._navigate_to_detail(detail_response)
        if not detail:
            self.logger.warning(f"Could not navigate detail response for {prop.get('address')}")
            return prop

        # --- HOA ---
        hoa = self._extract_hoa(detail)
        prop['hoa_fee_monthly'] = hoa.get('fee_monthly')
        prop['hoa_fee_frequency'] = hoa.get('frequency')
        prop['hoa_includes'] = hoa.get('includes', [])

        # --- Tax ---
        tax = self._extract_tax(detail)
        prop['annual_tax'] = tax.get('annual_amount')
        prop['tax_year'] = tax.get('year')

        # --- Year built ---
        prop['year_built'] = self._extract_year_built(detail)

        # --- Full description text ---
        full_description = self._extract_full_description(detail)
        if full_description:
            prop['full_description'] = full_description
            # Re-scan description for STR keywords and negative flags
            extra_keywords = self._scan_description_for_str_keywords(full_description)
            existing = prop.get('str_keywords_found', [])
            prop['str_keywords_found'] = list(dict.fromkeys(existing + extra_keywords))

            extra_negatives = self._scan_description_for_negatives(full_description)
            existing_neg = prop.get('negative_flags', [])
            prop['negative_flags'] = list(dict.fromkeys(existing_neg + extra_negatives))

        # --- Resort re-detection ---
        # The list-API scan often misses resort names because only tags &
        # community fields were available. Now that we have the full MLS
        # description, re-run the match on the combined text and upgrade
        # the resort label if it was "Unknown Resort".
        # Lazy import to avoid circular dependency (property_finder imports
        # from this module at top level).
        from property_finder import identify_resort

        current_resort = prop.get('resort_name', '') or ''
        if current_resort == 'Unknown Resort' or not current_resort:
            combined = ' '.join(filter(None, [
                prop.get('address', ''),
                full_description or '',
                ' '.join(prop.get('str_keywords_found', []) or []),
            ]))
            detected = identify_resort(combined)
            if detected:
                prop['resort_name'] = detected

        # --- Additional useful fields ---
        prop['price_per_sqft'] = self._extract_price_per_sqft(detail)
        prop['days_on_market'] = self._extract_days_on_market(detail)
        prop['parking'] = self._extract_parking(detail)
        prop['stories'] = self._extract_stories(detail)
        prop['cooling'] = self._extract_feature(detail, 'cooling')
        prop['heating'] = self._extract_feature(detail, 'heating')
        prop['pool'] = self._extract_pool(detail)
        prop['flood_risk'] = self._extract_risk(detail, 'flood')

        prop['enriched'] = True
        prop['enriched_at'] = datetime.now(timezone.utc).isoformat()

        return prop

    def _navigate_to_detail(self, response: Dict) -> Optional[Dict]:
        """Navigate the API response to find the property detail object.

        The v2 detail endpoint may return data in different structures.
        We try common paths.
        """
        # Path 1: response -> properties -> [0]
        props = response.get('properties')
        if isinstance(props, list) and props:
            return props[0]

        # Path 2: response -> data -> home
        data = response.get('data')
        if isinstance(data, dict):
            home = data.get('home')
            if isinstance(home, dict):
                return home

        # Path 3: response itself is the detail
        if 'property_id' in response or 'description' in response:
            return response

        # Path 4: response -> data -> property_detail
        if isinstance(data, dict):
            detail = data.get('property_detail')
            if isinstance(detail, dict):
                return detail

        return None

    def _extract_hoa(self, detail: Dict) -> Dict:
        """Extract HOA fee information."""
        result = {'fee_monthly': None, 'frequency': None, 'includes': []}

        # Try direct hoa field
        hoa = detail.get('hoa')
        if isinstance(hoa, dict):
            fee = hoa.get('fee') or hoa.get('amount')
            if fee is not None:
                freq = (hoa.get('frequency') or hoa.get('period') or '').lower()
                result['frequency'] = freq or 'monthly'
                result['fee_monthly'] = self._normalize_hoa_to_monthly(fee, freq)
                result['includes'] = hoa.get('includes', []) or []
                return result

        # Try nested under fees
        fees = detail.get('fees', [])
        if isinstance(fees, list):
            for fee_item in fees:
                if not isinstance(fee_item, dict):
                    continue
                fee_type = (fee_item.get('type') or '').lower()
                if 'hoa' in fee_type or 'association' in fee_type:
                    amount = fee_item.get('amount') or fee_item.get('fee')
                    freq = (fee_item.get('frequency') or '').lower()
                    if amount is not None:
                        result['fee_monthly'] = self._normalize_hoa_to_monthly(amount, freq)
                        result['frequency'] = freq or 'monthly'
                        return result

        # Try parsing from description text as last resort
        desc = self._extract_full_description(detail) or ''
        hoa_match = re.search(
            r'hoa\s*(?:fee|dues?)?\s*(?:is|are|of|:)?\s*\$?([\d,]+(?:\.\d{2})?)\s*(?:/\s*)?(monthly|annually|quarterly|per\s+month|per\s+year)?',
            desc, re.IGNORECASE
        )
        if hoa_match:
            amount = float(hoa_match.group(1).replace(',', ''))
            freq = (hoa_match.group(2) or 'monthly').lower().replace('per ', '')
            result['fee_monthly'] = self._normalize_hoa_to_monthly(amount, freq)
            result['frequency'] = freq
            return result

        return result

    def _normalize_hoa_to_monthly(self, amount: float, frequency: str) -> float:
        """Convert HOA fee to monthly equivalent."""
        amount = float(amount)
        frequency = (frequency or '').lower()
        if 'annual' in frequency or 'year' in frequency:
            return round(amount / 12, 2)
        elif 'quarter' in frequency:
            return round(amount / 3, 2)
        elif 'semi' in frequency:
            return round(amount / 6, 2)
        # Default: assume monthly
        return round(amount, 2)

    def _extract_tax(self, detail: Dict) -> Dict:
        """Extract tax assessment information."""
        result = {'annual_amount': None, 'year': None}

        tax = detail.get('tax_history')
        if isinstance(tax, list) and tax:
            # Most recent tax entry
            latest = tax[0]
            if isinstance(latest, dict):
                result['annual_amount'] = latest.get('tax') or latest.get('amount')
                result['year'] = latest.get('year')
                return result

        # Try alternative paths
        assessment = detail.get('assessment') or detail.get('tax')
        if isinstance(assessment, dict):
            result['annual_amount'] = assessment.get('tax') or assessment.get('amount')
            result['year'] = assessment.get('year')

        return result

    def _extract_year_built(self, detail: Dict) -> Optional[int]:
        """Extract year built."""
        # Direct field
        year = detail.get('year_built')
        if year:
            return int(year)

        # Under description
        desc_obj = detail.get('description', {})
        if isinstance(desc_obj, dict):
            year = desc_obj.get('year_built')
            if year:
                return int(year)

        return None

    def _extract_full_description(self, detail: Dict) -> Optional[str]:
        """Extract the full text description."""
        # Direct text field
        desc_obj = detail.get('description', {})
        if isinstance(desc_obj, dict):
            text = desc_obj.get('text')
            if text:
                return str(text)

        # Top-level text
        text = detail.get('description_text') or detail.get('remarks')
        if text:
            return str(text)

        return None

    def _extract_price_per_sqft(self, detail: Dict) -> Optional[float]:
        desc = detail.get('description', {})
        if isinstance(desc, dict):
            ppsf = desc.get('price_per_sqft')
            if ppsf:
                return float(ppsf)
        return None

    def _extract_days_on_market(self, detail: Dict) -> Optional[int]:
        dom = detail.get('days_on_market') or detail.get('list_date_delta')
        if dom is not None:
            return int(dom)
        return None

    def _extract_parking(self, detail: Dict) -> Optional[str]:
        desc = detail.get('description', {})
        if isinstance(desc, dict):
            garage = desc.get('garage')
            if garage:
                return f"{garage} garage"
        return None

    def _extract_stories(self, detail: Dict) -> Optional[int]:
        desc = detail.get('description', {})
        if isinstance(desc, dict):
            stories = desc.get('stories')
            if stories:
                return int(stories)
        return None

    def _extract_feature(self, detail: Dict, feature_name: str) -> Optional[str]:
        details_section = detail.get('details', [])
        if isinstance(details_section, list):
            for section in details_section:
                if not isinstance(section, dict):
                    continue
                if feature_name.lower() in (section.get('category', '') or '').lower():
                    items = section.get('text', [])
                    if isinstance(items, list):
                        return ', '.join(str(i) for i in items)
                    return str(items) if items else None
        return None

    def _extract_pool(self, detail: Dict) -> Optional[bool]:
        desc = detail.get('description', {})
        if isinstance(desc, dict):
            pool = desc.get('pool')
            if pool is not None:
                return bool(pool)
        # Check in tags or features
        tags = detail.get('tags', []) or []
        if any('pool' in str(t).lower() for t in tags):
            return True
        return None

    def _extract_risk(self, detail: Dict, risk_type: str) -> Optional[str]:
        """Extract risk data (flood, wildfire, etc.)."""
        local = detail.get('local', {})
        if isinstance(local, dict):
            risk = local.get(risk_type)
            if isinstance(risk, dict):
                severity = risk.get('severity') or risk.get('risk')
                if severity:
                    return str(severity)
        return None

    # ------------------------------------------------------------------
    # Description keyword scanning
    # ------------------------------------------------------------------

    def _scan_description_for_str_keywords(self, text: str) -> List[str]:
        """Scan full description text for STR-relevant keywords."""
        text_lower = text.lower()
        keywords = []
        patterns = [
            'investor friendly', 'investment property', 'investor',
            'short term rental', 'short-term rental', 'str permitted', 'str allowed',
            'vacation rental', 'vacation home', 'airbnb', 'vrbo',
            'turnkey', 'turn key', 'rental program', 'rental income',
            'resort', 'furnished', 'fully furnished',
            'community pool', 'clubhouse',
            'condo hotel', 'condotel', 'hotel condo',
            'rental pool', 'rental management',
            'no rental restrictions', 'no minimum lease',
        ]
        for kw in patterns:
            if kw in text_lower:
                keywords.append(kw)
        return keywords

    def _scan_description_for_negatives(self, text: str) -> List[str]:
        """Scan full description for negative STR indicators."""
        text_lower = text.lower()
        flags = []
        patterns = [
            'owner occupied only', 'no investors', 'long term rental only',
            'no short term', 'no rental', 'primary residence only',
            'not for investment', 'seasonal resident only',
            'minimum lease', 'annual lease required',
            'no airbnb', 'no vrbo', 'no vacation rental',
            'hoa restricts', 'hoa does not allow', 'hoa prohibits',
            'special assessment',
        ]
        for flag in patterns:
            if flag in text_lower:
                flags.append(flag)
        return flags


def calculate_adjusted_score(prop: Dict, base_score: float) -> float:
    """Re-calculate investment score factoring in enrichment data (HOA, tax, etc.).

    This is meant to be called after enrichment to adjust the original score.
    """
    score = base_score

    # HOA impact: penalize high HOA, bonus for low/no HOA
    hoa = prop.get('hoa_fee_monthly')
    if hoa is not None:
        if hoa == 0:
            score += 0.3  # No HOA is great for cash flow
        elif hoa <= 200:
            score += 0.1
        elif hoa <= 400:
            pass  # Neutral
        elif hoa <= 600:
            score -= 0.3
        else:
            score -= 0.6  # High HOA significantly hurts cash flow

    # Tax impact
    annual_tax = prop.get('annual_tax')
    if annual_tax is not None:
        monthly_tax = annual_tax / 12
        if monthly_tax > 500:
            score -= 0.2
        elif monthly_tax < 200:
            score += 0.1

    # Year built: newer is generally better for maintenance costs
    year_built = prop.get('year_built')
    if year_built is not None:
        if year_built >= 2015:
            score += 0.2
        elif year_built >= 2000:
            score += 0.1
        elif year_built < 1980:
            score -= 0.2

    # Pool bonus (desirable for STR guests near Disney)
    if prop.get('pool'):
        score += 0.2

    # Negative flags from description scanning
    negatives = prop.get('negative_flags', [])
    for flag in negatives:
        if any(term in flag for term in ['no airbnb', 'no vrbo', 'no vacation',
                                          'no short term', 'hoa prohibits',
                                          'hoa does not allow', 'hoa restricts']):
            score -= 1.5  # Major red flag — likely can't do STR
            break
    if 'special assessment' in negatives:
        score -= 0.5

    return max(0.0, min(10.0, round(score, 1)))
