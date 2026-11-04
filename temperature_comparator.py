from typing import Any, Dict, List, Optional
try:
    from .location_resolver import ResolvedLocation
except (ImportError, ValueError):
    from location_resolver import ResolvedLocation


class TemperatureComparator:
    """Strict temperature comparison engine conforming to SPEC-v1.3 §9."""

    @staticmethod
    def compute_delta(target_temp: float, baseline_temp: float) -> Dict[str, Any]:
        """Deterministic calculation and non-rigid change labels (SPEC §9.4, C22)."""
        delta = round(target_temp - baseline_temp, 1)
        abs_delta = abs(delta)

        # Non-rigid change heuristics (SPEC §9.4)
        if abs_delta < 3.0:
            magnitude = "minor"
        elif abs_delta < 5.0:
            magnitude = "notable"
        elif abs_delta < 8.0:
            magnitude = "significant"
        else:
            magnitude = "major"

        return {
            "delta_temp_c": delta,
            "change_magnitude": magnitude,
        }

    def validate_and_compare(
        self,
        target_loc: Optional[ResolvedLocation],
        baseline_loc: Optional[ResolvedLocation],
        target_temps: List[float],
        baseline_temps: List[float],
        target_window_hours: int,
        baseline_window_hours: int,
        target_kind: str,
        baseline_kind: str,
        aggregation: str = "mean_hourly",
    ) -> Dict[str, Any]:
        """Validate location, window duration, metrics, and compute comparison."""
        # 1. Location match validation (SPEC §9.2 #1, C17, C64)
        if not target_loc or not baseline_loc or target_loc.id != baseline_loc.id:
            return {
                "available": False,
                "reason": "mismatched_location",
            }

        # 2. Window duration match validation (SPEC §9.2 #2)
        if target_window_hours != baseline_window_hours:
            return {
                "available": False,
                "reason": "mismatched_time_window",
            }

        # 3. Sufficient data points validation (SPEC §9.2 #6)
        if not target_temps or not baseline_temps:
            return {
                "available": False,
                "reason": "insufficient_comparable_data",
            }

        # 4. Aggregation calculation (SPEC §9.2 #4)
        t_mean = round(sum(target_temps) / len(target_temps), 1)
        b_mean = round(sum(baseline_temps) / len(baseline_temps), 1)

        delta_info = self.compute_delta(t_mean, b_mean)

        return {
            "available": True,
            "baseline_temp_c": b_mean,
            "target_temp_c": t_mean,
            "delta_temp_c": delta_info["delta_temp_c"],
            "change_magnitude": delta_info["change_magnitude"],
            "aggregation": aggregation,
            "baseline_kind": baseline_kind,
            "target_kind": target_kind,
        }

    def build_clothing_evidence(
        self,
        weather_data: Dict[str, Any],
        comparison: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Construct structured clothing evidence for LLM dress advice (SPEC §9.5, C20)."""
        target_temp = weather_data.get("temperature_c")
        if target_temp is None:
            high = weather_data.get("temperature_max_c")
            low = weather_data.get("temperature_min_c")
            if high is not None and low is not None:
                target_temp = round((high + low) / 2, 1)

        rain_prob = weather_data.get("precipitation_probability")
        rain_mm = weather_data.get("precipitation_mm")
        condition = str(weather_data.get("condition") or "")
        rain_expected = (
            (rain_prob is not None and rain_prob >= 0.5)
            or (rain_mm is not None and rain_mm > 0.0)
            or ("雨" in condition)
        )

        return {
            "target_temperature_c": target_temp,
            "target_feels_like_c": weather_data.get("feels_like_c"),
            "target_wind_speed_ms": weather_data.get("wind_speed_ms"),
            "rain_expected": rain_expected,
            "comparison": comparison,
        }
