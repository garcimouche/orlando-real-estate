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
        """Identify if property is in a known resort"""
        address_lower = (address or '').lower()
        city_lower = (city or '').lower()
        
        # Build a combined search text from all available fields
        search_text = address_lower
        if prop:
            # Check community/subdivision name from API data
            location = prop.get('location', {}) or {}
            address_obj = location.get('address', {}) or {}
            # Some listings have community or subdivision info
            community = address_obj.get('community', '') or ''
            neighborhood = location.get('neighborhood', '') or ''
            search_text += ' ' + community.lower() + ' ' + neighborhood.lower()
            
            # Also check the description text
            description_obj = prop.get('description', {}) or {}
            # The API sometimes has a text field or name field
            for field in ['text', 'name', 'sub_type']:
                val = description_obj.get(field, '') or ''
                search_text += ' ' + val.lower()
            
            # Check tags for resort/community names
            tags = prop.get('tags', []) or []
            for tag in tags:
                search_text += ' ' + tag.lower()
        
        # Known STR-friendly resort communities in the Orlando/Kissimmee area.
        # Multi-word keys with word-boundary matching to avoid false positives.
        resorts = {
            # Established resort communities
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
            'orange lake': 'Orange Lake Resort',
            'encore resort': 'Encore Resort',
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

        # Check all text for resort mentions using word boundaries
        for resort_key, resort_name in resorts.items():
            if re.search(r'\b' + re.escape(resort_key) + r'\b', search_text):
                return resort_name
                
        return "Unknown Resort"
    
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
        
        # Sort by initial score to pick top candidates for enrichment
        processed_properties.sort(
            key=lambda x: x.get('investment_score', 0),
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
                surviving.append(prop)
            processed_properties = surviving

        # Final sort after re-scoring
        self.discovered_properties = sorted(
            processed_properties,
            key=lambda x: x.get('investment_score', 0),
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

    def get_top_str_properties(self, limit: int = 20) -> List[Dict]:
        """Get top N properties by STR investment score"""
        return [prop for prop in self.discovered_properties[:limit] 
                if prop.get('investment_score', 0) >= 4.0]
    
    def _estimate_nightly_rate(self, prop: dict) -> float:
        """Estimate nightly rate based on property characteristics"""
        base_rate = 100
        bedroom_multiplier = 1 + (prop.get('bedrooms', 1) - 1) * 0.2
        type_multiplier = {'Townhouse': 1.2, 'Condo': 1.0}.get(prop.get('property_type', ''), 1.0)
        sqft = prop.get('square_feet', 1000)
        size_factor = min(max(sqft / 1000, 0.7), 1.8)
        
        amenity_bonus = 0
        if prop.get('has_matterport'):
            amenity_bonus += 0.1
        if prop.get('virtual_tours_count', 0) > 0:
            amenity_bonus += 0.05
        if prop.get('is_new_listing'):
            amenity_bonus += 0.05
        
        estimated_rate = base_rate * bedroom_multiplier * type_multiplier * size_factor * (1 + amenity_bonus)
        return round(estimated_rate, 2)
    
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
        print(f"🎯 Showing top {len(top_properties)} STR opportunities\n")
        
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