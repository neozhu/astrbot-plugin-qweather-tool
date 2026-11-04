from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional


@dataclass
class ResolvedLocation:
    """Normalized resolved location conforming to SPEC-v1.3 §6.2.1."""
    id: str
    name: str
    adm1: str
    adm2: str
    country: str
    lat: float
    lon: float
    tz: str
    type: str = ""
    rank: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "adm1": self.adm1,
            "adm2": self.adm2,
            "country": self.country,
            "timezone": self.tz,
            "lat": self.lat,
            "lon": self.lon,
        }


@dataclass
class LocationResolutionResult:
    """Result of location resolution process."""
    status: str  # "ok", "need_location", "ambiguous_location", "not_found"
    location: Optional[ResolvedLocation] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    resolution_method: Optional[str] = None
    warning: Optional[str] = None


class LocationResolver:
    """Disambiguates and resolves locations via GeoAPI following SPEC-v1.3 §6."""

    RANK_AUTO_SELECT_THRESHOLD = 15  # Internal constant (SPEC §6.2.1)

    def __init__(self, client: Any):
        self.client = client

    def _parse_candidate(self, c: Dict[str, Any]) -> ResolvedLocation:
        raw_rank = c.get("rank")
        rank_val: Optional[int] = None
        if raw_rank is not None:
            try:
                rank_val = int(raw_rank)
            except (ValueError, TypeError):
                rank_val = None

        return ResolvedLocation(
            id=str(c.get("id", "")),
            name=str(c.get("name", "")),
            adm1=str(c.get("adm1", "")),
            adm2=str(c.get("adm2", "")),
            country=str(c.get("country", "")),
            lat=float(c.get("lat", 0.0)),
            lon=float(c.get("lon", 0.0)),
            tz=str(c.get("tz", "Asia/Shanghai")),
            type=str(c.get("type", "")),
            rank=rank_val,
        )

    def _filter_by_explicit_adm(
        self,
        candidates: List[ResolvedLocation],
        full_text: str,
    ) -> List[ResolvedLocation]:
        """Tier 1: Filter candidates against explicit administrative constraints (SPEC §6.2.1).
        Only treats adm1/adm2/country as an explicit modifier if it is distinct from candidate name.
        """
        matching_candidates = []
        for c in candidates:
            adm1_stem = re.sub(r"(省|市|区|县|自治区|特别行政区)$", "", c.adm1).strip()
            adm2_stem = re.sub(r"(省|市|区|县)$", "", c.adm2).strip()
            country_stem = re.sub(r"(国)$", "", c.country).strip()

            adm1_matched = bool(
                adm1_stem
                and len(adm1_stem) >= 2
                and adm1_stem != c.name
                and adm1_stem in full_text
            )
            adm2_matched = bool(
                adm2_stem
                and len(adm2_stem) >= 2
                and adm2_stem != c.name
                and adm2_stem in full_text
            )
            country_matched = bool(
                country_stem
                and len(country_stem) >= 2
                and country_stem != c.name
                and country_stem in full_text
            )

            if adm1_matched or adm2_matched or country_matched:
                matching_candidates.append(c)

        return matching_candidates if matching_candidates else candidates

    async def resolve(
        self,
        location_text: str,
        default_city: str = "",
        is_group: bool = False,
        confirmed_location: Optional[ResolvedLocation] = None,
    ) -> LocationResolutionResult:
        """Resolve location according to 5-tier priority matching chain."""
        text = (location_text or "").strip()

        # Step 1: Check empty location
        if not text:
            if not is_group and default_city:
                # Private chat can fall back to default_city
                return await self.resolve(
                    location_text=default_city,
                    default_city="",
                    is_group=False,
                    confirmed_location=confirmed_location,
                )
            return LocationResolutionResult(status="need_location")

        # Query GeoAPI
        raw_candidates = await self.client.search_city(location=text)
        if not raw_candidates:
            # If text had prefixes (e.g. 江苏苏州昆山), try searching trailing tokens
            for i in range(1, len(text)):
                sub = text[i:]
                if len(sub) >= 2:
                    raw_candidates = await self.client.search_city(location=sub)
                    if raw_candidates:
                        break

        if not raw_candidates:
            return LocationResolutionResult(status="need_location")

        parsed_candidates = [self._parse_candidate(c) for c in raw_candidates]

        # Tier 1: Explicit administrative constraints (hard constraint)
        parsed_candidates = self._filter_by_explicit_adm(parsed_candidates, text)

        # Tier 2: Confirmed location in current valid conversation (only same name)
        if confirmed_location:
            for c in parsed_candidates:
                if (c.name in text or text in c.name) and c.id == confirmed_location.id:
                    return LocationResolutionResult(
                        status="ok",
                        location=c,
                        resolution_method="confirmed_context",
                    )

        # Tier 3: Full name exact match
        exact_matches = [c for c in parsed_candidates if c.name == text or text.endswith(c.name)]
        if exact_matches:
            target_pool = exact_matches
            if len(target_pool) == 1:
                return LocationResolutionResult(
                    status="ok",
                    location=target_pool[0],
                    resolution_method="exact_name",
                )
        else:
            target_pool = parsed_candidates

        # Tier 4: Location type match (prefer type='city')
        city_types = [c for c in target_pool if c.type == "city"]
        if city_types and len(city_types) < len(target_pool):
            target_pool = city_types

        # Tier 5: Official rank sorting and auto-select threshold
        candidates_with_rank = [c for c in target_pool if c.rank is not None]
        candidates_without_rank = [c for c in target_pool if c.rank is None]

        candidates_with_rank.sort(key=lambda x: x.rank)  # type: ignore
        sorted_candidates = candidates_with_rank + candidates_without_rank

        if not sorted_candidates:
            return LocationResolutionResult(status="need_location")

        if len(sorted_candidates) == 1:
            return LocationResolutionResult(
                status="ok",
                location=sorted_candidates[0],
                resolution_method="exact_name" if sorted_candidates[0].name == text else "rank_priority",
            )

        # When multiple candidates exist:
        top = sorted_candidates[0]
        second = sorted_candidates[1]

        if top.rank is not None and second.rank is not None:
            rank_diff = second.rank - top.rank
            if rank_diff >= self.RANK_AUTO_SELECT_THRESHOLD:
                return LocationResolutionResult(
                    status="ok",
                    location=top,
                    resolution_method="rank_priority",
                )

        # Ambiguous location: return up to 3 candidates for natural clarification
        candidate_summaries = [
            {
                "name": c.name,
                "adm1": c.adm1,
                "adm2": c.adm2,
                "country": c.country,
                "id": c.id,
            }
            for c in sorted_candidates[:3]
        ]
        return LocationResolutionResult(
            status="ambiguous_location",
            candidates=candidate_summaries,
            location=None,
        )
