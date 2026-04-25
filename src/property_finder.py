#!/usr/bin/env python3
"""
Secure Enhanced Property Discovery Agent for Orlando STR Investments
Fixed version with proper error handling and improved features
API key loaded securely from .env file (not hardcoded!)
Using YOUR discovered endpoint: properties/v3/list
"""

import argparse
import requests
import json
import time
import re
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import logging
from dotenv import load_dotenv
import os

from property_enricher import PropertyEnricher, calculate_adjusted_score

# Load environment variables from .env file
load_dotenv()

# ---------------------------------------------------------------------------
# AirDNA-style per-zip STR rate estimates
# ---------------------------------------------------------------------------
# Structure: {zip_code: {bedrooms: (median_nightly_adr_usd, occupancy_rate)}}
#
# These are RESEARCH-BASED ESTIMATES from public Orlando STR market data.
# Replace with real AirDNA MarketMinder numbers when subscribed.
# Sources considered: AirDNA public previews, AirBtics summaries, Rabbu estimates,
# Orlando-Osceola tourist tax filings (for occupancy trends).
#
# ADR = Average Daily Rate that guests actually pay (not listing price).
# Occupancy = fraction of nights booked (already accounts for off-season).
#
# Net annual revenue = ADR × 365 × occupancy
# Net monthly gross  = ADR × 30  × occupancy  (used as revBruts in finance tool)
# ---------------------------------------------------------------------------
STR_RATES_BY_ZIP = {
    # --- Davenport resort corridor (ChampionsGate, Solterra, Windsor Island) ---
    '33896': {  # ChampionsGate — premium, newer, strong year-round
        3: (215, 0.72), 4: (290, 0.75), 5: (385, 0.73),
        6: (450, 0.70), 7: (520, 0.68), 8: (600, 0.65),
    },
    '33897': {  # Solterra, Windsor Island, Bella Vida, Aviana
        3: (180, 0.68), 4: (240, 0.72), 5: (315, 0.70),
        6: (380, 0.68), 7: (440, 0.66), 8: (510, 0.63),
    },
    '33837': {  # Davenport general — older stock, farther from parks
        2: (110, 0.58), 3: (140, 0.62), 4: (185, 0.65),
        5: (245, 0.63), 6: (290, 0.60),
    },
    # --- Kissimmee / Celebration corridor ---
    '34747': {  # Celebration, Formosa Gardens, Reunion, Windsor Hills
        2: (135, 0.68), 3: (185, 0.70), 4: (255, 0.73),
        5: (330, 0.72), 6: (395, 0.70), 7: (460, 0.68),
    },
    '34746': {  # Kissimmee west, Storey Lake, Emerald Island, Windsor Palms
        2: (115, 0.65), 3: (155, 0.68), 4: (210, 0.70),
        5: (275, 0.68), 6: (330, 0.66),
    },
    '34741': {  # Kissimmee east/central — more residential
        2: (100, 0.60), 3: (130, 0.63), 4: (175, 0.65),
        5: (225, 0.62),
    },
    # --- Universal / International Drive corridor ---
    '32819': {  # Universal, Dr. Phillips, I-Drive — condo hotels, high occ
        1: (105, 0.75), 2: (145, 0.73), 3: (190, 0.72),
        4: (245, 0.70), 5: (310, 0.67),
    },
    '32821': {  # Southern I-Drive, SeaWorld, Convention Center
        1: (95, 0.72), 2: (130, 0.70), 3: (165, 0.68),
        4: (215, 0.65), 5: (270, 0.62),
    },
    '32822': {  # Williamsburg — near Universal but more residential
        2: (110, 0.65), 3: (145, 0.63), 4: (190, 0.60),
    },
}

# Fallback for zip codes not in the table (rough average)
_DEFAULT_RATES_BY_BEDROOMS = {
    1: (100, 0.68), 2: (130, 0.65), 3: (165, 0.65), 4: (220, 0.67),
    5: (290, 0.66), 6: (350, 0.63), 7: (410, 0.60), 8: (475, 0.58),
}


# ---------------------------------------------------------------------------
# Known STR-friendly resort/community dictionary
# ---------------------------------------------------------------------------
# Maps lowercase search key → canonical display name. Keys use word-boundary
# matching so "vistana" won't match inside "vistanaxyz".
# Shared by both the list-data scanner (RealtyInUSAPI) and the post-enrichment
# re-scan (PropertyEnricher) — the enricher has access to the full MLS
# description text where community names most commonly appear.
# ---------------------------------------------------------------------------
KNOWN_RESORTS = {
    # Established Disney-corridor resort communities
    'windsor hills': 'Windsor Hills Resort',
    'windsor palms': 'Windsor Palms Resort',
    'windsor cay': 'Windsor Cay Resort',
    'windsor island': 'Windsor Island Resort',
    'windsor at westside': 'Windsor at Westside',
    'terra verde': 'Terra Verde Resort',
    'storey lake': 'Storey Lake',
    'solterra': 'Solterra Resort',
    'solara resort': 'Solara Resort',
    'solara': 'Solara Resort',
    'vista cay': 'Vista Cay Resort',
    'celebration': 'Celebration',
    'reunion resort': 'Reunion Resort',
    'reunion': 'Reunion Resort',
    'orange lake': 'Orange Lake Resort',
    'encore resort': 'Encore Resort',
    'encore club': 'Encore Resort',
    'paradise palms': 'Paradise Palms Resort',
    'champions gate': 'ChampionsGate Resort',
    'championsgate': 'ChampionsGate Resort',
    'regal palms': 'Regal Palms Resort',
    'regal oaks': 'Regal Oaks Resort',
    'bahama bay': 'Bahama Bay Resort',
    'westgate': 'Westgate Resort',
    'emerald island': 'Emerald Island Resort',
    'terracotta': 'Terracotta Resort',
    'lake berkley': 'Lake Berkley Resort',
    'rolling hills': 'Rolling Hills',
    'greater groves': 'Greater Groves',
    'kingdom ridge': 'Kingdom Ridge',
    'four corners': 'Four Corners',
    'west haven': 'West Haven Resort',
    'vacation village': 'Vacation Village Resort',
    'aviana resort': 'Aviana Resort',
    'aviana': 'Aviana Resort',
    # Vacation home communities
    'encantada': 'Encantada Resort',
    'bella vida': 'Bella Vida Resort',
    'bella piazza': 'Bella Piazza Resort',
    'highlands reserve': 'Highlands Reserve',
    'ridgewood lakes': 'Ridgewood Lakes',
    'indian creek': 'Indian Creek',
    'tuscan hills': 'Tuscan Hills',
    'watersong': 'Watersong Resort',
    'veranda palms': 'Veranda Palms',
    'calabay parc': 'Calabay Parc',
    'crystal cove': 'Crystal Cove Resort',
    'cumbrian lakes': 'Cumbrian Lakes',
    'hampton lakes': 'Hampton Lakes',
    'eagle pointe': 'Eagle Pointe',
    'legacy park': 'Legacy Park',
    'sunset lakes': 'Sunset Lakes',
    # Condo-hotel / resort-style
    'cypress pointe': 'Cypress Pointe Resort',
    'floridays': 'Floridays Resort',
    'mystic dunes': 'Mystic Dunes Resort',
    'liki tiki': 'Liki Tiki Village',
    'caribe cove': 'Caribe Cove Resort',
    # Universal / International Drive corridor
    'point orlando': 'The Point Orlando Resort',
    'the point orlando': 'The Point Orlando Resort',
    'international drive': 'I-Drive Corridor',
    'i-drive': 'I-Drive Corridor',
    'idrive': 'I-Drive Corridor',
    'sand lake': 'Sand Lake / Dr. Phillips',
    'dr phillips': 'Dr. Phillips',
    'doctor phillips': 'Dr. Phillips',
    'universal palms': 'Universal Palms Resort',
    'parkway palms': 'Parkway Palms Resort',
    'palm pointe': 'Palm Pointe Resort',
    'williamsburg': 'Williamsburg Condos',
    'caribe royale': 'Caribe Royale Resort',
    'sheraton vistana': 'Sheraton Vistana',
    'vistana': 'Sheraton Vistana',
    'worldquest': 'WorldQuest Resort',
    'world quest': 'WorldQuest Resort',
}

# Sort keys by length descending so longer/more-specific names match first
# ("champions gate" before "champions", "sheraton vistana" before "vistana")
_RESORT_KEYS_BY_LENGTH = sorted(KNOWN_RESORTS.keys(), key=len, reverse=True)


def identify_resort(text: str) -> Optional[str]:
    """Scan a text blob (address + description + tags etc.) for known resort
    names. Returns the canonical resort name or None if nothing matches.

    Longer keys are tried first so "championsgate" wins over "champions".
    """
    if not text:
        return None
    text_lower = text.lower()
    for key in _RESORT_KEYS_BY_LENGTH:
        if re.search(r'\b' + re.escape(key) + r'\b', text_lower):
            return KNOWN_RESORTS[key]
    return None


def estimate_str_revenue(zip_code: str, bedrooms: int) -> dict:
    """Return (median_adr, occupancy, monthly_gross) for a given zip+bedroom.

    Uses AirDNA-style per-zip rates if available, else the bedroom-only fallback.
    """
    bedrooms = max(1, min(bedrooms or 3, 8))
    zip_rates = STR_RATES_BY_ZIP.get(zip_code)
    if zip_rates and bedrooms in zip_rates:
        adr, occ = zip_rates[bedrooms]
        source = 'airdna_zip'
    elif zip_rates:
        # Zip known but bedroom count not in table — use nearest
        nearest = min(zip_rates.keys(), key=lambda b: abs(b - bedrooms))
        adr, occ = zip_rates[nearest]
        source = 'airdna_zip_interpolated'
    else:
        adr, occ = _DEFAULT_RATES_BY_BEDROOMS.get(bedrooms, (165, 0.65))
        source = 'bedroom_fallback'

    monthly_gross = round(adr * 30 * occ, 2)
    return {
        'adr': adr,
        'occupancy': occ,
        'monthly_gross': monthly_gross,
        'source': source,
    }


def estimate_cashflow(
    prop: dict,
    down_payment_pct: float = 30,
    mortgage_rate_pct: float = 7.5,
    mgmt_pct: float = 20,
    insurance_monthly: float = 180,
    maintenance_monthly: float = 150,
    default_hoa_monthly: float = 300,
) -> Optional[float]:
    """Estimate monthly cashflow (USD) for a property, mirroring the
    ``computeBase().cfBrut`` formula in property_finance.jsx at its default
    slider values so the Python ranking matches what the UI shows when the
    property is first opened.

    Returns None when essential data (price, revenue estimate) is missing.
    """
    prix = prop.get('price') or 0
    rev_bruts = prop.get('estimated_monthly_gross') or 0
    if prix <= 0 or rev_bruts <= 0:
        return None

    hoa = prop.get('hoa_fee_monthly')
    if hoa is None:
        hoa = default_hoa_monthly
    t_occ_pct = (prop.get('estimated_occupancy') or 0.68) * 100

    down = prix * down_payment_pct / 100
    loan = prix - down
    mr = mortgage_rate_pct / 100 / 12
    if loan > 0 and mr > 0:
        hypo = loan * (mr * (1 + mr) ** 360) / ((1 + mr) ** 360 - 1)
    else:
        hypo = 0

    # Mirror JSX: property taxes estimated as 1.5%/yr of price, ignoring
    # detail-endpoint `annual_tax`, so the ranking matches the default UI view.
    taxes_fonc = prix * 0.015 / 12
    rev_net = rev_bruts * t_occ_pct / 100
    gestion = rev_net * mgmt_pct / 100
    charges_op = gestion + hoa + taxes_fonc + insurance_monthly + maintenance_monthly
    cf_brut = rev_net - charges_op - hypo
    return round(cf_brut, 2)


class RealtyInUSAPI:
    """Handler for Realty-in-US API integration"""
    
    def __init__(self, api_key: str = None):
        # Load API key from environment variable (secure!)
        self.api_key = api_key or os.getenv('REALTY_API_KEY')
        if not self.api_key:
            raise ValueError("REALTY_API_KEY not found in environment. Please set it in .env file.")
        
        self.base_url = "https://realty-in-us.p.rapidapi.com"
        self.endpoint = "/properties/v3/list"
        self.headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": "realty-in-us.p.rapidapi.com",
            "Content-Type": "application/json"
        }
        self.logger = logging.getLogger(__name__)
        # Count every HTTP request made (retries included).
        self.api_call_count = 0
    
    def search_properties(self, city: str = None, state_code: str = "FL",
                         postal_code: str = None,
                         min_price: int = None, max_price: int = None,
                         min_beds: int = None, max_beds: int = None,
                         property_types: List[str] = None,
                         limit: int = 20) -> List[Dict]:
        """
        Search for properties using the working endpoint you discovered.

        Accepts either city+state_code or postal_code as location filter.
        Returns list of property dictionaries in the format expected by enhanced agent.
        """

        # Load defaults from environment if not specified
        if min_price is None:
            min_price = int(os.getenv('MIN_PRICE', 150000))
        if max_price is None:
            max_price = int(os.getenv('MAX_PRICE', 415000))
        if min_beds is None:
            min_beds = int(os.getenv('MIN_BEDROOMS', 1))
        if max_beds is None:
            max_beds = int(os.getenv('MAX_BEDROOMS', 5))
        if property_types is None:
            property_types = ["apartment", "condo_townhome", "condo_townhome_rowhome_coop"]

        url = f"{self.base_url}{self.endpoint}"

        payload = {
            "limit": limit,
            "offset": 0,
            "type": property_types,
            "beds": {"max": max_beds, "min": min_beds},
            "status": ["for_sale"],
            "list_price": {"max": max_price, "min": min_price},
            "sort": {
                "direction": "desc",
                "field": "list_date"
            }
        }

        # Use postal_code or city+state as location filter
        if postal_code:
            payload["postal_code"] = postal_code
            location_label = f"zip {postal_code}"
        else:
            payload["city"] = city
            payload["state_code"] = state_code
            location_label = f"{city}, {state_code}"

        self.logger.info(f"Searching {location_label} for properties ${min_price:,}-${max_price:,}")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                self.api_call_count += 1
                response = requests.post(url, headers=self.headers, json=payload, timeout=15)

                if response.status_code == 200:
                    data = response.json()
                    properties = self._extract_properties_from_response(data)
                    self.logger.info(f"Found {len(properties)} properties in {location_label}")
                    return properties
                elif response.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    wait = 2 ** attempt
                    self.logger.warning(f"API returned {response.status_code}, retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                else:
                    self.logger.error(f"API Error {response.status_code}: {response.text}")
                    return []

            except requests.exceptions.Timeout:
                if attempt < max_retries:
                    self.logger.warning(f"Request timed out, retrying ({attempt + 1}/{max_retries})...")
                    time.sleep(2 ** attempt)
                    continue
                self.logger.error("Request timed out after all retries")
                return []
            except Exception as e:
                self.logger.error(f"Error searching properties: {e}")
                return []

        return []
    
    def _extract_properties_from_response(self, data: dict) -> List[Dict]:
        """Extract and format property data from API response"""
        properties = []
        
        try:
            # Navigate to the results array: data → home_search → results
            if ('data' in data and 
                'home_search' in data['data'] and 
                'results' in data['data']['home_search']):
                
                results = data['data']['home_search']['results']
                
                for prop in results:
                    # Skip if prop is None or not a dict
                    if not isinstance(prop, dict):
                        continue
                    formatted_prop = self._format_property_for_enhanced_agent(prop)
                    if formatted_prop:
                        properties.append(formatted_prop)
                        
            else:
                self.logger.warning("Unexpected response structure")
                
        except Exception as e:
            self.logger.error(f"Error extracting properties: {e}")
            
        return properties
    
    def _format_property_for_enhanced_agent(self, prop: dict) -> Optional[Dict]:
        """Format a property from API response to match enhanced agent expectations"""
        try:
            # Extract basic info
            property_id = prop.get('property_id', '')
            listing_id = prop.get('listing_id', '')
            
            # Price - use list_price or sold_price
            price = prop.get('list_price') or prop.get('sold_price')
            if price is None:
                return None
            
            # Location info
            location = prop.get('location') or {}
            if not isinstance(location, dict):
                location = {}
            address_info = location.get('address') or {}
            if not isinstance(address_info, dict):
                address_info = {}
            
            address_line = address_info.get('line', '')
            city = address_info.get('city', '')
            state_code = address_info.get('state_code', '')
            postal_code = address_info.get('postal_code', '')
            
            # Build full address
            address_parts = [address_line, city, state_code, postal_code]
            full_address = ", ".join([part for part in address_parts if part])
            
            # Description/info - extract from description object
            description_obj = prop.get('description') or {}
            if not isinstance(description_obj, dict):
                description_obj = {}
            beds = description_obj.get('beds')
            baths_full = description_obj.get('baths_full', 0)
            baths_half = description_obj.get('baths_half', 0)
            baths_full = baths_full or 0
            baths_half = baths_half or 0
            baths_total = baths_full + (baths_half * 0.5)
            sqft = description_obj.get('sqft')
            lot_sqft = description_obj.get('lot_sqft')
            property_type_raw = description_obj.get('type') or ''
            sub_type = description_obj.get('sub_type') or ''
            
            # Validate required fields - be lenient, skip only if critical data missing
            if not price or not full_address:
                return None
            if beds is None:
                beds = 0  # Default, will be filtered later
            if sqft is None:
                sqft = 0  # Default, will be filtered later
            baths_total = baths_total or 0
            
            # Determine property type for filtering
            property_type = self._map_property_type(property_type_raw, sub_type)
            if property_type not in ['condo', 'townhouse']:
                return None  # Skip if not condo/townhouse
            
            # Build description text for keyword search (using structured data)
            description_text = self._build_description_text(description_obj, prop)
            
            # Extract URL for due diligence
            listing_url = prop.get('href', '')
            
            # Get coordinates if available
            coordinate = location.get('coordinate') or {}
            latitude = coordinate.get('lat')
            longitude = coordinate.get('lon')
            
            # Estimate value
            estimate_val = None
            estimate_data = prop.get('estimate')
            if isinstance(estimate_data, dict):
                estimate_val = estimate_data.get('estimate')
            
            # Virtual tours
            virtual_tours = prop.get('virtual_tours', [])
            if not isinstance(virtual_tours, list):
                virtual_tours = []
            virtual_tours_count = len(virtual_tours)
            has_matterport = bool(prop.get('matterport', False))
            
            # Photo count
            photo_count = prop.get('photo_count', 0)
            if not isinstance(photo_count, (int, float)) or photo_count < 0:
                photo_count = 0
            
            # Flags
            flags = prop.get('flags', {})
            if not isinstance(flags, dict):
                flags = {}
            is_new_listing = bool(flags.get('is_new_listing', False))
            is_price_reduced = bool(flags.get('is_price_reduced', False))
            
            # Build the property dictionary in enhanced agent format
            formatted_property = {
                'id': f"realtor_{property_id}_{listing_id}",
                'address': full_address,
                'price': int(price),
                'property_type': property_type.title(),  # Condo/Townhouse
                'bedrooms': int(beds),
                'bathrooms': float(baths_total),
                'square_feet': int(sqft) if sqft else 0,
                'description': description_text,
                'resort_name': self._identify_resort_from_address(full_address, city, prop),
                'str_keywords_found': self._find_str_keywords_in_data(description_obj, prop),
                'negative_flags': self._find_negative_flags_in_data(description_obj, prop),
                'source': 'realtor_com_api',
                'listing_url': listing_url,
                'date_found': datetime.now().isoformat(),
                'raw_data': json.dumps(prop, default=str)[:1000],  # For debugging
                # Additional useful fields
                'lot_sqft': lot_sqft,
                'latitude': latitude,
                'longitude': longitude,
                'list_date': prop.get('list_date'),
                'estimate': estimate_val,
                'virtual_tours_count': virtual_tours_count,
                'has_matterport': has_matterport,
                'photo_count': photo_count,
                'is_new_listing': is_new_listing,
                'is_price_reduced': is_price_reduced
            }
            
            return formatted_property
            
        except Exception as e:
            self.logger.error(f"Error formatting property: {e}", exc_info=True)
            return None
    
    def _map_property_type(self, type_raw: str, sub_type: str) -> str:
        """Map API property types to our standard types"""
        type_raw = (type_raw or "").lower()
        sub_type = (sub_type or "").lower()
        
        # Map various property type representations
        if any(t in type_raw for t in ['condo', 'apartment']) or \
           any(t in sub_type for t in ['condo', 'apartment']):
            return 'condo'
        elif any(t in type_raw for t in ['townhome', 'townhouse', 'rowhome']) or \
             any(t in sub_type for t in ['townhome', 'townhouse', 'rowhome']):
            return 'townhouse'
        else:
            return type_raw or 'unknown'
    
    def _build_description_text(self, description_obj: dict, prop: dict) -> str:
        """Build a description-like text from structured data for keyword matching"""
        parts = []
        
        # Add property type info
        if description_obj.get('type'):
            parts.append(f"{description_obj['type']}")
        if description_obj.get('sub_type'):
            parts.append(f"{description_obj['sub_type']}")
            
        # Add bed/bath info
        beds = description_obj.get('beds')
        baths_full = description_obj.get('baths_full') or 0
        baths_half = description_obj.get('baths_half') or 0
        if beds is not None:
            parts.append(f"{beds} bedroom{'s' if beds != 1 else ''}")
        bath_parts = []
        if baths_full > 0:
            bath_parts.append(f"{baths_full} full bath{'s' if baths_full != 1 else ''}")
        if baths_half > 0:
            bath_parts.append(f"{baths_half} half bath{'s' if baths_half != 1 else ''}")
        if bath_parts:
            parts.append(" ".join(bath_parts))
            
        # Add square footage
        sqft = description_obj.get('sqft')
        if sqft:
            parts.append(f"{sqft} sqft")
            
        # Add lot size
        lot_sqft = description_obj.get('lot_sqft')
        if lot_sqft:
            parts.append(f"{lot_sqft} sqft lot")
            
        # Add property details from flags
        flags = prop.get('flags', {})
        if flags.get('is_new_listing'):
            parts.append("New listing")
        if flags.get('is_price_reduced'):
            parts.append("Price reduced")
            
        # Add estimate if available
        estimate = (prop.get('estimate') or {}).get('estimate')
        if estimate:
            parts.append(f"Estimated value: ${estimate:,}")
            
        # Add virtual tour info
        virtual_tours = prop.get('virtual_tours', [])
        if virtual_tours:
            parts.append(f"{len(virtual_tours)} virtual tour{'s' if len(virtual_tours) != 1 else ''}")
            
        if prop.get('matterport'):
            parts.append("Matterport 3D tour available")
            
        # Add photo count
        photo_count = prop.get('photo_count') or 0
        if photo_count > 0:
            parts.append(f"{photo_count} photos")
            
        return ". ".join(parts) + "." if parts else "Property details available"
    
    def _identify_resort_from_address(self, address: str, city: str, prop: dict = None) -> str:
        """Identify if property is in a known resort from list-API data.

        Delegates the actual dictionary match to the module-level
        ``identify_resort()`` so the enricher can reuse the same logic
        on the richer full-description text it fetches later.
        """
        address_lower = (address or '').lower()

        # Build a combined search text from all available fields
        search_text = address_lower
        if prop:
            # Check community/subdivision name from API data
            location = prop.get('location', {}) or {}
            address_obj = location.get('address', {}) or {}
            community = address_obj.get('community', '') or ''
            neighborhood = location.get('neighborhood', '') or ''
            search_text += ' ' + community.lower() + ' ' + neighborhood.lower()

            # Also check the description text
            description_obj = prop.get('description', {}) or {}
            for field in ['text', 'name', 'sub_type']:
                val = description_obj.get(field, '') or ''
                search_text += ' ' + val.lower()

            # Check tags for resort/community names
            tags = prop.get('tags', []) or []
            for tag in tags:
                search_text += ' ' + tag.lower()

        return identify_resort(search_text) or "Unknown Resort"
    
    def _find_str_keywords_in_data(self, description_obj: dict, prop: dict) -> List[str]:
        """Find STR/investor keywords in the property data"""
        keywords_found = []
        
        # Build comprehensive search text from ALL available fields
        search_parts = []
        
        # Add description fields
        for field in ['type', 'sub_type', 'text', 'name']:
            val = description_obj.get(field, '') or ''
            if val:
                search_parts.append(str(val).lower())
        
        # Add flags
        flags = prop.get('flags', {}) or {}
        for flag_key, flag_val in flags.items():
            if flag_val and isinstance(flag_val, bool):
                search_parts.append(flag_key.replace('_', ' '))
        
        # Add tags — this is where STR keywords often appear
        tags = prop.get('tags', []) or []
        for tag in tags:
            search_parts.append(str(tag).lower())
        
        # Add community/neighborhood info from location
        location = prop.get('location', {}) or {}
        address_obj = location.get('address', {}) or {}
        for field in ['community', 'neighborhood']:
            val = address_obj.get(field, '') or ''
            if val:
                search_parts.append(str(val).lower())
        
        search_text = ' '.join(search_parts)
        
        # STR/investor keyword detection — comprehensive list
        str_keywords = [
            # Explicit STR keywords
            'investor friendly', 'investment property', 'investor',
            'short term rental', 'short-term rental', 'str permitted', 'str allowed',
            'vacation rental', 'vacation home', 'airbnb', 'vrbo',
            'turnkey', 'turn key', 'rental program', 'rental income',
            # Resort/STR community indicators
            'resort', 'resort style', 'resort amenities',
            'vacation', 'furnished', 'fully furnished',
            'community pool', 'clubhouse', 'tennis',
            # Condo hotel indicators
            'condo hotel', 'condotel', 'hotel condo',
            'rental pool', 'rental management',
        ]
        
        # Negative keywords (things that disqualify STR)
        negative_patterns = [
            'owner occupied only', 'no investors', 'long term rental only',
            'no short term', 'no rental', 'primary residence only',
            'not for investment', 'seasonal resident only',
        ]
        
        for keyword in str_keywords:
            if keyword in search_text:
                keywords_found.append(keyword)
        
        # Remove duplicates preserving order
        return list(dict.fromkeys(keywords_found))
    
    def _find_negative_flags_in_data(self, description_obj: dict, prop: dict) -> List[str]:
        """Find negative indicators in the property data"""
        flags_found = []
        
        # Check flags for potential issues
        flags = prop.get('flags', {})
        if not isinstance(flags, dict):
            flags = {}
        
        # Check if it's been on market a long time (indirect)
        list_date_str = prop.get('list_date')
        if list_date_str:
            try:
                list_date = datetime.fromisoformat(list_date_str.replace('Z', '+00:00'))
                days_on_market = (datetime.now(timezone.utc) - list_date.astimezone(timezone.utc)).days
                if days_on_market > 90:  # Been listed > 3 months
                    flags_found.append("long_listing_period")
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Could not parse list_date '{list_date_str}': {e}")
        
        # Check price reductions (could indicate issues)
        if flags.get('is_price_reduced'):
            flags_found.append("price_reduced")
            
        return flags_found


class EnhancedPropertyDiscoveryAgent:
    """
    Enhanced agent that uses REAL DATA from the API you discovered
    Configuration loaded securely from .env file
    """
    
    def __init__(self, api_key: str = None, enrich: bool = True, local_mode: bool = False):
        # Initialize API handler (will load key from .env)
        self.local_mode = local_mode
        self.api_handler = RealtyInUSAPI(api_key)
        self.enricher = PropertyEnricher(api_key, local_mode=local_mode) if enrich else None
        self.logger = logging.getLogger(__name__)
        self.discovered_properties = []

        # List-level cache path (saves raw search results for --local mode)
        cache_dir = os.path.join(os.path.dirname(__file__), '..', 'cache')
        os.makedirs(cache_dir, exist_ok=True)
        self._list_cache_path = os.path.join(cache_dir, 'property_list.json')
        
        # Load target cities from environment or use defaults
        cities_env = os.getenv('TARGET_CITIES', 'Kissimmee,Davenport,Celebration,Orlando')
        self.target_cities = [(city.strip(), 'FL') for city in cities_env.split(',')]

        # STR-focused zip codes covering Disney AND Universal/I-Drive corridors
        # Disney/Kissimmee: 34746, 34747, 34741, 33896, 33897, 33837
        # Universal/I-Drive: 32819 (Universal, I-Drive, Dr. Phillips),
        #                    32821 (Southern I-Drive, SeaWorld),
        #                    32822 (Williamsburg / near Universal east)
        zips_env = os.getenv(
            'TARGET_ZIPS',
            '34746,34747,34741,33896,33897,33837,32819,32821,32822'
        )
        self.target_zips = [z.strip() for z in zips_env.split(',') if z.strip()]
        
        # Load investment criteria from environment
        self.max_price = int(os.getenv('MAX_PRICE', 415000))
        self.min_price = int(os.getenv('MIN_PRICE', 150000))
        self.property_types = ['condo', 'townhouse']
        self.min_bedrooms = int(os.getenv('MIN_BEDROOMS', 1))

        # STR signal filter: only consider properties with STR/investor hints
        # Set REQUIRE_STR_SIGNALS=false in .env to disable
        self.require_str_signals = os.getenv('REQUIRE_STR_SIGNALS', 'true').lower() == 'true'

        # Zip codes known to be STR-friendly resort corridors.
        # Properties in these zips pass the STR filter automatically
        # (the enrichment phase will catch anti-STR flags later).
        str_zips_env = os.getenv(
            'STR_FRIENDLY_ZIPS',
            '34746,34747,33896,33897,32819,32821,32822'
        )
        self.str_friendly_zips = set(z.strip() for z in str_zips_env.split(',') if z.strip())

        # Target areas from Franck's research
        self.target_areas = [
            'Kissimmee', 'Davenport', 'Celebration', 'Orlando',
            'Lake Buena Vista', 'Four Corners', 'Clermont',
            'International Drive', 'Universal',
        ]
    
    def search_properties(self) -> List[Dict]:
        """Main search method - finds properties using REAL API data (or cache).

        Searches by zip code for better coverage of resort/vacation
        communities near Disney, rather than by city name.

        In --local mode, loads previously cached list results instead of calling
        the API, allowing offline re-scoring without consuming API quota.
        """
        all_properties = []

        if self.local_mode:
            # Load raw list results from disk cache
            if os.path.exists(self._list_cache_path):
                try:
                    with open(self._list_cache_path, 'r') as f:
                        all_properties = json.load(f)
                    self.logger.info(
                        f"[LOCAL] Loaded {len(all_properties)} properties from list cache"
                    )
                except (json.JSONDecodeError, OSError) as e:
                    self.logger.error(f"Could not load list cache: {e}")
                    return []
            else:
                self.logger.error(
                    "List cache not found. Run without --local first to populate it."
                )
                return []
        else:
            search_limit = int(os.getenv('SEARCH_LIMIT_PER_ZIP', 200))
            self.logger.info("Starting REAL PROPERTY search for Orlando STR investments")
            self.logger.info(f"Targeting {len(self.target_zips)} zip codes, {search_limit} per zip")

            # Search each target zip code
            for zip_code in self.target_zips:
                self.logger.info(f"Searching zip {zip_code}...")

                properties = self.api_handler.search_properties(
                    postal_code=zip_code,
                    min_price=self.min_price,
                    max_price=self.max_price,
                    limit=search_limit
                )

                self.logger.info(f"Found {len(properties)} properties in zip {zip_code}")
                all_properties.extend(properties)

                time.sleep(0.5)  # Be respectful to API

            # Persist raw list results so --local mode can use them later
            try:
                with open(self._list_cache_path, 'w') as f:
                    json.dump(all_properties, f, default=str)
                self.logger.info(
                    f"Saved {len(all_properties)} raw properties to list cache"
                )
            except OSError as e:
                self.logger.warning(f"Could not save list cache: {e}")

        # Deduplicate properties across zip codes (by id and address)
        seen_ids = set()
        seen_addresses = set()
        unique_properties = []
        for prop in all_properties:
            pid = prop.get('id')
            addr = prop.get('address', '').lower().strip()
            if pid and pid in seen_ids:
                continue
            if addr and addr in seen_addresses:
                continue
            if pid:
                seen_ids.add(pid)
            if addr:
                seen_addresses.add(addr)
            unique_properties.append(prop)

        self.logger.info(f"Deduplicated: {len(all_properties)} → {len(unique_properties)} properties")

        # Process and score all properties
        processed_properties = []
        filtered_no_str = 0
        for prop in unique_properties:
            processed_prop = self._process_property_for_str_investment(prop)
            if processed_prop:
                processed_properties.append(processed_prop)
            elif self.require_str_signals and not self._has_str_signals(prop):
                filtered_no_str += 1

        if self.require_str_signals and filtered_no_str > 0:
            self.logger.info(
                f"STR filter: {filtered_no_str} properties rejected (no STR signals)"
            )
        
        # Sort by preliminary cashflow to pick top candidates for enrichment
        # (HOA not yet known, so estimate_cashflow uses default HOA=300 for all).
        processed_properties.sort(
            key=lambda x: x.get('estimated_monthly_cashflow') if x.get('estimated_monthly_cashflow') is not None else -1e9,
            reverse=True
        )

        # Enrich top candidates with detail data (HOA, tax, year built, etc.)
        enrich_limit = int(os.getenv('ENRICH_LIMIT', 25))
        if self.enricher:
            self.logger.info(f"Enriching top {enrich_limit} properties with detail data...")
            processed_properties = self.enricher.enrich_properties(
                processed_properties, limit=enrich_limit
            )
            # Re-score enriched properties and disqualify anti-STR ones
            anti_str_flags = [
                'no airbnb', 'no vrbo', 'no vacation rental',
                'no short term', 'hoa prohibits', 'hoa does not allow',
                'hoa restricts', 'primary residence only', 'owner occupied only',
                'no investors', 'annual lease required',
            ]
            surviving = []
            for prop in processed_properties:
                if prop.get('enriched'):
                    negatives = prop.get('negative_flags', [])
                    disqualified = any(
                        any(anti in neg for anti in anti_str_flags)
                        for neg in negatives
                    )
                    if disqualified:
                        self.logger.info(
                            f"DISQUALIFIED (anti-STR): {prop.get('address')} "
                            f"— flags: {negatives}"
                        )
                        continue
                    base_score = prop.get('investment_score', 0)
                    prop['investment_score'] = calculate_adjusted_score(prop, base_score)
                    # Recompute cashflow now that real HOA is known.
                    prop['estimated_monthly_cashflow'] = estimate_cashflow(prop)
                surviving.append(prop)
            processed_properties = surviving

        # Final sort: best investing opportunities first — by estimated monthly
        # cashflow (USD, before Canadian taxes). Properties with no cashflow
        # estimate sink to the bottom.
        self.discovered_properties = sorted(
            processed_properties,
            key=lambda x: x.get('estimated_monthly_cashflow') if x.get('estimated_monthly_cashflow') is not None else -1e9,
            reverse=True
        )

        self.logger.info(f"Discovered {len(self.discovered_properties)} STR-qualified properties")
        return self.discovered_properties
    
    def _has_str_signals(self, prop: dict) -> bool:
        """Check if a property has any STR/investor-friendly signals.

        A property passes if it has at least one of:
        - STR keyword found in listing data (tags, description, flags)
        - Located in a known resort community
        - Located in a zip code known to have STR-friendly communities

        This is the permissive discovery-time gate — the zip-code fallback
        lets us still score properties in STR-friendly geographies when the
        list API text didn't contain explicit keywords.
        """
        # Check STR keywords
        str_keywords = prop.get('str_keywords_found', [])
        if str_keywords:
            return True

        # Check known resort
        resort = prop.get('resort_name', '')
        if resort and resort != 'Unknown Resort':
            return True

        # Check if in a STR-friendly zip code
        address = prop.get('address', '')
        for zip_code in self.str_friendly_zips:
            if zip_code in address:
                return True

        return False

    def _has_explicit_str_signals(self, prop: dict) -> bool:
        """Strong STR signal — explicit keywords in the listing data or a
        recognized resort community. Does NOT accept zip-code-only matches.

        Used for the finance-tool export so we don't surface properties that
        passed the permissive gate purely on geography.
        """
        if prop.get('str_keywords_found'):
            return True
        resort = prop.get('resort_name', '')
        if resort and resort != 'Unknown Resort':
            return True
        return False

    def _process_property_for_str_investment(self, prop: dict) -> Optional[Dict]:
        """Process a property to determine its STR investment potential"""
        try:
            price = prop.get('price', 0)
            if price < self.min_price or price > self.max_price:
                return None

            bedrooms = prop.get('bedrooms', 0)
            if bedrooms < self.min_bedrooms:
                return None

            property_type = prop.get('property_type', '').lower()
            if property_type not in [pt.lower() for pt in self.property_types]:
                return None

            # STR signal gate: reject properties with no STR/investor hints
            if self.require_str_signals and not self._has_str_signals(prop):
                return None

            investment_score = self._calculate_str_score(prop)

            if investment_score >= 3.0:
                prop['investment_score'] = round(investment_score, 1)
                prop['score_timestamp'] = datetime.now().isoformat()

                # Attach revenue + preliminary cashflow so downstream sorts
                # can rank by cashflow. Pre-enrichment HOA is unknown, so
                # estimate_cashflow falls back to the default HOA; the value
                # is recomputed after enrichment once the real HOA is known.
                rev = self._estimate_str_revenue(prop)
                prop['estimated_nightly'] = rev['adr']
                prop['estimated_occupancy'] = rev['occupancy']
                prop['estimated_monthly_gross'] = rev['monthly_gross']
                prop['revenue_estimate_source'] = rev['source']
                prop['zip_used'] = rev['zip_used']
                prop['estimated_monthly_cashflow'] = estimate_cashflow(prop)
                return prop
            else:
                return None

        except Exception as e:
            self.logger.warning(f"Error processing property: {e}")
            return None
    
    def _calculate_str_score(self, prop: dict) -> float:
        """Calculate STR investment score based on available data.

        Weights (max points sum to 10.0):
          Price: 2.5, Type: 1.5, Bedrooms: 2.0, Bathrooms: 1.0,
          Sqft: 1.0, Location: 1.0, Resort: 0.5, New listing: 0.2,
          Virtual tours: 0.2, Photos: 0.1
        """
        score = 0.0

        # 1. Price score (max 2.5)
        price = prop.get('price', self.max_price)
        price_range = self.max_price - self.min_price
        if price_range > 0:
            price_ratio = max(0.0, min(1.0, (self.max_price - price) / price_range))
        else:
            price_ratio = 0.5
        score += price_ratio * 2.5

        # 2. Property type score (max 1.5)
        property_type = prop.get('property_type', '').lower()
        if property_type == 'townhouse':
            score += 1.5
        elif property_type == 'condo':
            score += 1.0

        # 3. Bedroom score (max 2.0)
        bedrooms = prop.get('bedrooms', 1)
        if bedrooms >= 3:
            score += 2.0
        elif bedrooms == 2:
            score += 1.5
        elif bedrooms == 1:
            score += 0.5

        # 4. Bathroom score (max 1.0)
        bathrooms = prop.get('bathrooms', 1.0)
        if bathrooms >= 2.5:
            score += 1.0
        elif bathrooms >= 2.0:
            score += 0.7
        elif bathrooms >= 1.5:
            score += 0.4

        # 5. Square footage score (max 1.0)
        sqft = prop.get('square_feet', 0)
        if sqft > 0 and 800 <= sqft <= 2000:
            score += 1.0
        elif sqft > 0:
            score += 0.5

        # 6. Location score (max 1.0)
        address = prop.get('address', '').lower()
        for area in self.target_areas:
            if area.lower() in address:
                score += 1.0
                break

        # 7. Resort detection bonus (max 0.5)
        resort_name = prop.get('resort_name', '')
        if resort_name and resort_name != "Unknown Resort":
            score += 0.5

        # 8. New listing bonus (max 0.2)
        if prop.get('is_new_listing'):
            score += 0.2

        # 9. Virtual tour/Matterport bonus (max 0.2)
        if prop.get('virtual_tours_count', 0) > 0:
            score += 0.1
        if prop.get('has_matterport'):
            score += 0.1

        # 10. Photo count bonus (max 0.1)
        photo_count = prop.get('photo_count', 0)
        if photo_count >= 20:
            score += 0.1
        elif photo_count >= 10:
            score += 0.05

        return score
    
    @property
    def total_api_calls(self) -> int:
        """Total HTTP calls made across list search and detail enrichment."""
        list_calls = self.api_handler.api_call_count
        detail_calls = self.enricher.api_call_count if self.enricher else 0
        return list_calls + detail_calls

    def get_top_str_properties(self, limit: int = 20,
                               require_explicit_signals: bool = False,
                               max_per_resort: int = 3) -> List[Dict]:
        """Get top N properties by STR investment score.

        Returns all ranked properties up to limit — no score threshold,
        so the finance tool can compare why #20 ranks lower than #1.

        When ``require_explicit_signals`` is True, drop properties whose
        only STR signal is the zip-code fallback (i.e. keep only those
        with explicit listing keywords or a known resort match).

        ``max_per_resort`` caps how many properties from the same named
        resort appear in the result (defaults to 3). Properties without a
        recognized resort ("Unknown Resort" / empty) are not capped, since
        they represent distinct standalone listings.
        """
        pool = self.discovered_properties
        if require_explicit_signals:
            pool = [p for p in pool if self._has_explicit_str_signals(p)]
        if max_per_resort and max_per_resort > 0:
            seen: Dict[str, int] = {}
            capped: List[Dict] = []
            for p in pool:
                resort = (p.get('resort_name') or '').strip()
                if resort and resort != 'Unknown Resort':
                    if seen.get(resort, 0) >= max_per_resort:
                        continue
                    seen[resort] = seen.get(resort, 0) + 1
                capped.append(p)
            pool = capped
        return list(pool[:limit])

    def export_scored_properties(self, limit: int = 20,
                                  output_path: str = None,
                                  require_explicit_signals: bool = True,
                                  max_per_resort: int = 3) -> str:
        """Write the top-N ranked properties to cache/scored_properties.json.

        This file is consumed by property_finance.html (the web-based
        investment analyzer). Contains everything needed to render the
        Discovery tab + pre-fill the finance sliders.

        By default we only export properties with *explicit* STR signals
        (listing keywords or a recognized resort). Zip-code-only matches
        are dropped so the finance tool doesn't analyze generic Davenport
        homes that merely sit in a STR-friendly market.

        Returns the path written.
        """
        if output_path is None:
            output_path = os.path.join(
                os.path.dirname(__file__), '..', 'cache', 'scored_properties.json'
            )
        last_run_path = os.path.join(
            os.path.dirname(output_path), 'last_run_ids.json'
        )

        # Load previous run snapshot (used to compute run-over-run delta).
        # Missing file = first run; we won't flag anything as new.
        previous_snapshot = None
        if os.path.exists(last_run_path):
            try:
                with open(last_run_path) as f:
                    previous_snapshot = json.load(f)
            except (OSError, json.JSONDecodeError):
                previous_snapshot = None

        top = self.get_top_str_properties(
            limit, require_explicit_signals=require_explicit_signals,
            max_per_resort=max_per_resort,
        )
        if require_explicit_signals:
            dropped = sum(
                1 for p in self.discovered_properties[:limit]
                if not self._has_explicit_str_signals(p)
            )
            if dropped:
                self.logger.info(
                    f"Export filter: dropped {dropped} zip-only matches "
                    f"(kept {len(top)} with explicit STR signals)"
                )
        previous_ids = set((previous_snapshot or {}).get('ids', []))
        previous_entries = (previous_snapshot or {}).get('entries', [])
        previous_by_id = {e['id']: e for e in previous_entries if e.get('id')}
        current_ids = {p.get('id') for p in top if p.get('id')}

        export = []
        for rank, prop in enumerate(top, 1):
            rev = self._estimate_str_revenue(prop)
            pid = prop.get('id')
            is_new_this_run = bool(
                previous_snapshot is not None and pid and pid not in previous_ids
            )
            export.append({
                'rank': rank,
                'is_new_this_run': is_new_this_run,
                # Core identity
                'id': prop.get('id'),
                'address': prop.get('address'),
                'listing_url': prop.get('listing_url'),
                'source': prop.get('source'),
                # Size & type
                'price': prop.get('price'),
                'bedrooms': prop.get('bedrooms'),
                'bathrooms': prop.get('bathrooms'),
                'square_feet': prop.get('square_feet'),
                'property_type': prop.get('property_type'),
                'lot_sqft': prop.get('lot_sqft'),
                # STR / discovery signals
                'resort_name': prop.get('resort_name'),
                'investment_score': prop.get('investment_score'),
                'str_keywords_found': prop.get('str_keywords_found', []),
                'negative_flags': prop.get('negative_flags', []),
                'zip_used': rev['zip_used'],
                # NEW! tracking
                'first_seen_at': prop.get('first_seen_at'),
                'detail_is_new': prop.get('detail_is_new', False),
                'is_new_listing': prop.get('is_new_listing', False),
                'is_price_reduced': prop.get('is_price_reduced', False),
                # Media
                'photo_count': prop.get('photo_count', 0),
                'virtual_tours_count': prop.get('virtual_tours_count', 0),
                'has_matterport': prop.get('has_matterport', False),
                # Enriched details (may be None if not enriched)
                'hoa_fee_monthly': prop.get('hoa_fee_monthly'),
                'hoa_includes': prop.get('hoa_includes', []),
                'annual_tax': prop.get('annual_tax'),
                'tax_year': prop.get('tax_year'),
                'year_built': prop.get('year_built'),
                'pool': prop.get('pool'),
                'flood_risk': prop.get('flood_risk'),
                'days_on_market': prop.get('days_on_market'),
                'price_per_sqft': prop.get('price_per_sqft'),
                'parking': prop.get('parking'),
                'stories': prop.get('stories'),
                'cooling': prop.get('cooling'),
                'heating': prop.get('heating'),
                'full_description': prop.get('full_description'),
                'enriched': prop.get('enriched', False),
                # AirDNA-style revenue estimate (feeds finance tool's revBruts)
                'estimated_nightly': rev['adr'],
                'estimated_occupancy': rev['occupancy'],
                'estimated_monthly_gross': rev['monthly_gross'],
                'revenue_estimate_source': rev['source'],
                # Monthly cashflow (USD) used as the primary ranking key,
                # mirroring computeBase.cfBrut at default sliders.
                'estimated_monthly_cashflow': prop.get('estimated_monthly_cashflow'),
            })

        dropped = []
        if previous_snapshot is not None:
            for prev in previous_entries:
                if prev.get('id') and prev['id'] not in current_ids:
                    dropped.append({
                        'id': prev.get('id'),
                        'address': prev.get('address'),
                        'resort_name': prev.get('resort_name'),
                        'previous_rank': prev.get('rank'),
                    })

        new_count = sum(1 for p in export if p['is_new_this_run'])

        payload = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'total_api_calls': self.total_api_calls,
            'local_mode': self.local_mode,
            'new_badge_window_days': int(os.getenv('NEW_BADGE_WINDOW_DAYS', 7)),
            'count': len(export),
            'properties': export,
            'previous_run_at': (previous_snapshot or {}).get('generated_at'),
            'has_baseline': previous_snapshot is not None,
            'new_this_run_count': new_count,
            'dropped': dropped,
            'dropped_count': len(dropped),
        }

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(payload, f, indent=2, default=str)

        # Persist this run's snapshot for next run's delta.
        snapshot = {
            'generated_at': payload['generated_at'],
            'ids': sorted(i for i in current_ids if i),
            'entries': [
                {
                    'id': p['id'],
                    'address': p['address'],
                    'resort_name': p['resort_name'],
                    'rank': p['rank'],
                }
                for p in export if p.get('id')
            ],
        }
        with open(last_run_path, 'w') as f:
            json.dump(snapshot, f, indent=2, default=str)

        self.logger.info(
            f"Exported {len(export)} scored properties to {output_path} "
            f"(new this run: {new_count}, dropped: {len(dropped)})"
        )
        return output_path

    def _extract_zip_from_address(self, address: str) -> Optional[str]:
        """Extract 5-digit zip from an address string."""
        if not address:
            return None
        match = re.search(r'\b(3[0-9]{4})\b', address)
        return match.group(1) if match else None

    def _estimate_str_revenue(self, prop: dict) -> dict:
        """Return AirDNA-style revenue estimate for a property.

        Returns dict with: adr, occupancy, monthly_gross, source.
        Uses the property's zip code for per-market rates, with small
        premium/discount for size, property type, and amenities.
        """
        zip_code = self._extract_zip_from_address(prop.get('address', ''))
        base = estimate_str_revenue(zip_code, prop.get('bedrooms', 3))

        # Small multipliers on top of the zip baseline
        multiplier = 1.0
        # Townhouse/standalone slightly premium over condo
        if prop.get('property_type', '').lower() == 'townhouse':
            multiplier += 0.05
        # Size premium — large units (>1.5× typical for their bedroom count)
        sqft = prop.get('square_feet') or 0
        beds = prop.get('bedrooms') or 3
        typical_sqft = beds * 450  # rough heuristic
        if sqft > typical_sqft * 1.3:
            multiplier += 0.05
        # Better listings likely achieve better rates
        if prop.get('has_matterport'):
            multiplier += 0.03

        adjusted_adr = round(base['adr'] * multiplier, 2)
        adjusted_monthly = round(adjusted_adr * 30 * base['occupancy'], 2)

        return {
            'adr': adjusted_adr,
            'occupancy': base['occupancy'],
            'monthly_gross': adjusted_monthly,
            'source': base['source'],
            'zip_used': zip_code,
        }

    def _estimate_nightly_rate(self, prop: dict) -> float:
        """Legacy shim — returns just the nightly ADR."""
        return self._estimate_str_revenue(prop)['adr']
    
    def print_str_opportunities(self, limit: int = 20):
        """Print formatted STR opportunities ready for investment review"""
        print("=" * 80)
        print("🏆 ENHANCED ORLANDO STR PROPERTY DISCOVERY")
        print("   Powered by REAL API Data (Secure Configuration)")
        print("   (Adapted from Franck's Research Principles)")
        print("=" * 80)
        
        top_properties = self.get_top_str_properties(limit)
        
        if not top_properties:
            print("❌ No STR-qualified properties found meeting criteria")
            return
        
        print(f"📊 Found {len(self.discovered_properties)} total properties")
        print(f"🎯 Showing top {len(top_properties)} STR opportunities")
        print(f"   📈 Ranked by estimated monthly cashflow (best first)\n")
        
        # Load financing terms from environment
        down_payment_pct = float(os.getenv('DOWN_PAYMENT_PCT', 25))
        interest_rate = float(os.getenv('INTEREST_RATE', 7.25))
        
        for i, prop in enumerate(top_properties, 1):
            new_badge = " ✨ NEW!" if prop.get('detail_is_new') else ""
            print(f"🥇 OPPORTUNITY #{i}{new_badge}")
            print(f"   📍 {prop['address']}")
            print(f"   💰 Price: ${prop['price']:,}")
            print(f"   🏠 {prop['bedrooms']}BR/{prop['bathrooms']}BA | {prop['square_feet']:,} sqft")
            print(f"   🏷️  Type: {prop['property_type']}")
            print(f"   🏨 Resort: {prop.get('resort_name', 'Unknown')}")
            
            # Show why this property qualified as STR-friendly
            str_kw = prop.get('str_keywords_found', [])
            resort = prop.get('resort_name', '')
            signals = []
            if resort and resort != 'Unknown Resort':
                signals.append(f"Resort: {resort}")
            if str_kw:
                signals.append(f"Keywords: {', '.join(str_kw[:5])}")
            if signals:
                print(f"   ✅ STR Signals: {' | '.join(signals)}")

            if prop.get('year_built'):
                print(f"   📅 Year Built: {prop['year_built']}")

            if prop.get('is_new_listing'):
                print(f"   🆕 NEW LISTING")
            if prop.get('is_price_reduced'):
                print(f"   💰 PRICE REDUCED")

            # Enriched data
            if prop.get('enriched'):
                hoa = prop.get('hoa_fee_monthly')
                if hoa is not None:
                    includes = prop.get('hoa_includes', [])
                    inc_str = f" (includes: {', '.join(includes)})" if includes else ""
                    print(f"   🏢 HOA: ${hoa:,.0f}/mo{inc_str}")
                else:
                    print(f"   🏢 HOA: Not listed")

                tax = prop.get('annual_tax')
                if tax is not None:
                    print(f"   🏛️  Tax: ${tax:,.0f}/yr (${tax/12:,.0f}/mo)")

                if prop.get('pool'):
                    print(f"   🏊 Pool: Yes")
                if prop.get('flood_risk'):
                    print(f"   🌊 Flood Risk: {prop['flood_risk']}")

                negatives = prop.get('negative_flags', [])
                if negatives:
                    print(f"   ⚠️  Flags: {', '.join(negatives)}")

            amenities = []
            if prop.get('has_matterport'):
                amenities.append("Matterport 3D Tour")
            if prop.get('virtual_tours_count', 0) > 0:
                amenities.append(f"{prop['virtual_tours_count']} Virtual Tour{'s' if prop['virtual_tours_count'] > 1 else ''}")
            if prop.get('photo_count', 0) > 0:
                amenities.append(f"{prop['photo_count']} Photos")
            if amenities:
                print(f"   📸 Amenities: {', '.join(amenities)}")
                
            print(f"   📊 STR Investment Score: {prop.get('investment_score', 0):.1f}/10.0")
            
            estimated_nightly = self._estimate_nightly_rate(prop)
            estimated_monthly_gross = estimated_nightly * 30 * 0.7
            estimated_monthly_net = estimated_monthly_gross * 0.65
            # Proper mortgage calculation: P&I on loan amount
            loan_amount = prop['price'] * (1 - down_payment_pct/100)
            monthly_rate = interest_rate / 100 / 12  # Annual rate → monthly
            if monthly_rate > 0:
                n_payments = 30 * 12  # 30-year fixed
                monthly_mortgage = loan_amount * (monthly_rate * (1 + monthly_rate)**n_payments) / ((1 + monthly_rate)**n_payments - 1)
            else:
                monthly_mortgage = loan_amount / (30 * 12)
            monthly_mortgage_estimate = monthly_mortgage
            
            # Fixed monthly costs
            hoa_monthly = prop.get('hoa_fee_monthly') or 0
            tax_monthly = (prop.get('annual_tax') or 0) / 12

            print(f"   💵 Est. Nightly Rate: ${estimated_nightly}")
            print(f"   💰 Est. Monthly Gross: ${estimated_monthly_gross:,.0f} (70% occ)")
            print(f"   💰 Est. Monthly Net: ${estimated_monthly_net:,.0f} (after 35% expenses)")
            print(f"   🏦 Est. Monthly Mortgage: ${monthly_mortgage_estimate:,.0f} ({down_payment_pct:.0f}% down, {interest_rate}% rate, 30yr)")
            if hoa_monthly > 0 or tax_monthly > 0:
                print(f"   🏢 Monthly HOA: ${hoa_monthly:,.0f} | 🏛️  Monthly Tax: ${tax_monthly:,.0f}")
            total_fixed = monthly_mortgage_estimate + hoa_monthly + tax_monthly
            cash_flow = estimated_monthly_net - total_fixed
            cash_flow_color = '🟢' if cash_flow > 0 else '🔴'
            print(f"   {cash_flow_color} Est. Monthly Cash Flow: ${cash_flow:,.0f} (after mortgage + HOA + tax)")
            # Ranking cashflow — matches the finance UI at default sliders
            # and is what drives the sort order.
            ranking_cf = prop.get('estimated_monthly_cashflow')
            if ranking_cf is not None:
                ranking_color = '🟢' if ranking_cf > 0 else '🔴'
                print(f"   {ranking_color} Ranking Cash Flow (UI defaults): ${ranking_cf:,.0f}/mo")
            
            print(f"   🔗 Source: {prop['source']}")
            print(f"   🌐 {prop.get('listing_url', 'URL available in full data')}")
            print()
        
        print("=" * 80)
        print("📊 NEXT STEPS:")
        print("   1. Run financial analysis on top picks using str_calculator.py")
        print("   2. Verify specific buildings within resorts for HOA rules")
        print("   3. Check actual rental comps in each area")
        print("   4. Consider setting up weekly automated scans")
        print("   5. Review property pages via provided URLs for due diligence")
        mode_label = "[LOCAL — no API calls]" if self.local_mode else ""
        print(f"\n📡 Total API calls this run: {self.total_api_calls} {mode_label}".rstrip())
        print("=" * 80)

def demo_enhanced_usage(local: bool = False):
    """Demonstrate the enhanced agent with REAL API data (or local cache)."""
    print("🚀 ENHANCED ORLANDO STR PROPERTY DISCOVERY AGENT")
    if local:
        print("   Mode: LOCAL (running entirely from cache — no API calls)")
    else:
        print("   Powered by REAL API Data (Secure Configuration from .env)")
    print("   (Adapted from Franck's Research Findings)")
    print("=" * 70)

    # Initialize agent (will load config from .env)
    agent = EnhancedPropertyDiscoveryAgent(local_mode=local)

    print("🔧 Agent Configuration (from .env):")
    print(f"   💰 Price Range: ${agent.min_price:,} - ${agent.max_price:,}")
    print(f"   🏠 Property Types: {', '.join(agent.property_types)}")
    print(f"   🛏️  Min Bedrooms: {agent.min_bedrooms}")
    print(f"   🎯 Target Zips: {', '.join(agent.target_zips)}")
    print()

    # Run search
    if local:
        print("📂 LOADING PROPERTIES FROM CACHE...")
    else:
        print("🔍 SEARCHING FOR REAL PROPERTIES...")
    properties = agent.search_properties()

    # Show results
    agent.print_str_opportunities(limit=20)

    # Export JSON for the web-based finance analyzer
    try:
        export_path = agent.export_scored_properties(limit=20)
        print(f"\n📦 Exported scored list → {export_path}")
        print("   View in browser: ./src/start_web_finance.sh")
    except Exception as e:
        print(f"\n⚠️  Could not export scored properties: {e}")

    print("\n" + "=" * 70)
    if not local:
        print("✅ AGENT READY FOR PRODUCTION USE WITH REAL DATA")
        print("🔧 Configuration is loaded securely from .env file")
        print("   (API key and settings are NOT in source code)")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Orlando STR Property Discovery Agent"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Run entirely from cache (no API calls). Requires a previous live run.",
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    demo_enhanced_usage(local=args.local)