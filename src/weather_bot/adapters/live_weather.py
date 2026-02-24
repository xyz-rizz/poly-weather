from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from weather_bot.core.interfaces import ForecastSource, ObservationSource
from weather_bot.models.domain import ForecastPoint, ForecastSnapshot, ObservationSnapshot
from weather_bot.utils.http import JsonHttpClient


def _parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(UTC)


def _c_to_f(value_c: float) -> float:
    return (value_c * 9 / 5) + 32


def _to_f(temp: float | int, unit: str | None) -> float:
    if unit and unit.upper().startswith("C"):
        return _c_to_f(float(temp))
    return float(temp)


def _best_period_for_target(periods: list[dict[str, Any]], target_time_utc: datetime) -> dict[str, Any] | None:
    containing: list[dict[str, Any]] = []
    for period in periods:
        try:
            start = _parse_dt(period["startTime"])
            end = _parse_dt(period["endTime"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= target_time_utc < end:
            containing.append(period)
    if containing:
        return containing[0]

    best_period: dict[str, Any] | None = None
    best_delta: float | None = None
    for period in periods:
        try:
            start = _parse_dt(period["startTime"])
        except (KeyError, TypeError, ValueError):
            continue
        delta = abs((start - target_time_utc).total_seconds())
        if best_delta is None or delta < best_delta:
            best_period = period
            best_delta = delta
    return best_period


def _payload_updated_time(payload: dict[str, Any], default: datetime) -> datetime:
    props = payload.get("properties", {}) if isinstance(payload, dict) else {}
    for key in ("updated", "generatedAt", "updateTime"):
        val = props.get(key)
        if isinstance(val, str):
            try:
                return _parse_dt(val)
            except ValueError:
                continue
    return default


def _period_pop_pct(period: dict[str, Any]) -> float | None:
    pop = period.get("probabilityOfPrecipitation", {})
    if isinstance(pop, dict) and isinstance(pop.get("value"), (int, float)):
        return max(0.0, min(100.0, float(pop["value"])))
    return None


def _period_cloud_cover_pct(period: dict[str, Any]) -> float | None:
    sky = period.get("skyCover", {})
    if isinstance(sky, dict) and isinstance(sky.get("value"), (int, float)):
        return max(0.0, min(100.0, float(sky["value"])))
    txt = " ".join(
        str(period.get(k) or "") for k in ("shortForecast", "detailedForecast")
    ).lower()
    if not txt:
        return None
    if any(w in txt for w in ("overcast", "mostly cloudy")):
        return 80.0
    if any(w in txt for w in ("cloudy", "partly cloudy", "partly sunny")):
        return 55.0
    if any(w in txt for w in ("sunny", "clear")):
        return 15.0
    return None


def _period_weather_risk_score(period: dict[str, Any]) -> float | None:
    txt = " ".join(
        str(period.get(k) or "") for k in ("shortForecast", "detailedForecast")
    ).lower()
    pop = _period_pop_pct(period) or 0.0
    risk = 0.0
    if pop >= 20:
        risk += min(0.5, pop / 200.0)
    if any(w in txt for w in ("thunder", "storm")):
        risk += 0.35
    if any(w in txt for w in ("showers", "rain", "drizzle", "precip")):
        risk += 0.20
    if any(w in txt for w in ("fog", "mist", "haze")):
        risk += 0.10
    cloud = _period_cloud_cover_pct(period)
    if cloud is not None and cloud >= 70:
        risk += 0.10
    risk = max(0.0, min(1.0, risk))
    return risk if risk > 0 else None


@dataclass(frozen=True)
class CityWeatherConfig:
    city: str
    station_id: str
    latitude: float
    longitude: float


class NwsHourlyForecastSource(ForecastSource):
    def __init__(
        self,
        city_configs: list[CityWeatherConfig],
        *,
        http_client: JsonHttpClient | None = None,
        source_name: str = "nws-hourly",
    ) -> None:
        self.name = source_name
        self._cities: dict[str, list[CityWeatherConfig]] = {}
        for cfg in city_configs:
            self._cities.setdefault(cfg.city, []).append(cfg)
        self._http = http_client or JsonHttpClient()
        self._points_payload_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._hourly_url_cache: dict[tuple[str, str], str] = {}

    def fetch_forecasts(self, cities: list[str], target_time_utc: datetime) -> ForecastSnapshot:
        points: list[ForecastPoint] = []
        created = datetime.now(UTC)
        for city in cities:
            cfgs = self._cities.get(city) or []
            if not cfgs:
                continue
            for cfg in cfgs:
                forecast_url = self._get_hourly_forecast_url(cfg)
                payload = self._http.get_json(forecast_url)
                periods = payload.get("properties", {}).get("periods", [])
                period = _best_period_for_target(periods, target_time_utc)
                if not period:
                    continue

                temp_f = _to_f(period.get("temperature"), period.get("temperatureUnit"))
                prob_precip = period.get("probabilityOfPrecipitation", {})
                confidence = None
                if isinstance(prob_precip, dict):
                    val = prob_precip.get("value")
                    if isinstance(val, (int, float)):
                        confidence = max(0.2, min(0.95, 1.0 - (float(val) / 150.0)))

                points.append(
                    ForecastPoint(
                        source=self.name,
                        station_id=cfg.station_id,
                        city=city,
                        target_time_utc=target_time_utc,
                        expected_temp_f=float(temp_f),
                        low_f=float(temp_f) - 2.5,
                        high_f=float(temp_f) + 2.5,
                        confidence=confidence,
                        updated_at_utc=_payload_updated_time(payload, created),
                        pop_pct=_period_pop_pct(period),
                        cloud_cover_pct=_period_cloud_cover_pct(period),
                        weather_risk_score=_period_weather_risk_score(period),
                    )
                )
        return ForecastSnapshot(points=points, created_at_utc=created)

    def _get_hourly_forecast_url(self, cfg: CityWeatherConfig) -> str:
        key = (cfg.city, cfg.station_id)
        if key in self._hourly_url_cache:
            return self._hourly_url_cache[key]
        payload = self._get_points_payload(cfg)
        forecast_url = payload.get("properties", {}).get("forecastHourly")
        if not isinstance(forecast_url, str):
            raise ValueError(f"No forecastHourly URL for city={cfg.city}")
        self._hourly_url_cache[key] = forecast_url
        return forecast_url

    def _get_points_payload(self, cfg: CityWeatherConfig) -> dict[str, Any]:
        key = (cfg.city, cfg.station_id)
        if key in self._points_payload_cache:
            return self._points_payload_cache[key]
        points_url = f"https://api.weather.gov/points/{cfg.latitude},{cfg.longitude}"
        payload = self._http.get_json(points_url)
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected points payload for city={cfg.city}")
        self._points_payload_cache[key] = payload
        return payload


class NwsDailyHighForecastSource(ForecastSource):
    def __init__(
        self,
        city_configs: list[CityWeatherConfig],
        *,
        http_client: JsonHttpClient | None = None,
        source_name: str = "nws-daily-high",
    ) -> None:
        self.name = source_name
        self._cities: dict[str, list[CityWeatherConfig]] = {}
        for cfg in city_configs:
            self._cities.setdefault(cfg.city, []).append(cfg)
        self._http = http_client or JsonHttpClient()
        self._points_payload_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._daily_url_cache: dict[tuple[str, str], str] = {}

    def fetch_forecasts(self, cities: list[str], target_time_utc: datetime) -> ForecastSnapshot:
        points: list[ForecastPoint] = []
        created = datetime.now(UTC)
        for city in cities:
            cfgs = self._cities.get(city) or []
            if not cfgs:
                continue
            for cfg in cfgs:
                forecast_url = self._get_daily_forecast_url(cfg)
                payload = self._http.get_json(forecast_url)
                periods = payload.get("properties", {}).get("periods", [])
                if not isinstance(periods, list) or not periods:
                    continue
                daytime = [p for p in periods if isinstance(p, dict) and p.get("isDaytime") is True]
                period = _best_period_for_target(daytime or periods, target_time_utc)
                if not period:
                    continue
                temp = period.get("temperature")
                if not isinstance(temp, (int, float)):
                    continue
                temp_f = _to_f(temp, period.get("temperatureUnit"))
                points.append(
                    ForecastPoint(
                        source=self.name,
                        station_id=cfg.station_id,
                        city=city,
                        target_time_utc=target_time_utc,
                        expected_temp_f=float(temp_f),
                        low_f=float(temp_f) - 4.5,
                        high_f=float(temp_f) + 4.5,
                        confidence=0.65,
                        updated_at_utc=_payload_updated_time(payload, created),
                        pop_pct=_period_pop_pct(period),
                        cloud_cover_pct=_period_cloud_cover_pct(period),
                        weather_risk_score=_period_weather_risk_score(period),
                    )
                )
        return ForecastSnapshot(points=points, created_at_utc=created)

    def _get_daily_forecast_url(self, cfg: CityWeatherConfig) -> str:
        key = (cfg.city, cfg.station_id)
        if key in self._daily_url_cache:
            return self._daily_url_cache[key]
        payload = self._get_points_payload(cfg)
        forecast_url = payload.get("properties", {}).get("forecast")
        if not isinstance(forecast_url, str):
            raise ValueError(f"No forecast URL for city={cfg.city}")
        self._daily_url_cache[key] = forecast_url
        return forecast_url

    def _get_points_payload(self, cfg: CityWeatherConfig) -> dict[str, Any]:
        key = (cfg.city, cfg.station_id)
        if key in self._points_payload_cache:
            return self._points_payload_cache[key]
        points_url = f"https://api.weather.gov/points/{cfg.latitude},{cfg.longitude}"
        payload = self._http.get_json(points_url)
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected points payload for city={cfg.city}")
        self._points_payload_cache[key] = payload
        return payload


class NwsHourlyPathHighForecastSource(ForecastSource):
    def __init__(
        self,
        city_configs: list[CityWeatherConfig],
        *,
        http_client: JsonHttpClient | None = None,
        source_name: str = "nws-hourly-path-high",
    ) -> None:
        self.name = source_name
        self._cities: dict[str, list[CityWeatherConfig]] = {}
        for cfg in city_configs:
            self._cities.setdefault(cfg.city, []).append(cfg)
        self._http = http_client or JsonHttpClient()
        self._points_payload_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._hourly_url_cache: dict[tuple[str, str], str] = {}

    def fetch_forecasts(self, cities: list[str], target_time_utc: datetime) -> ForecastSnapshot:
        created = datetime.now(UTC)
        now_utc = created
        points: list[ForecastPoint] = []
        for city in cities:
            cfgs = self._cities.get(city) or []
            if not cfgs:
                continue
            for cfg in cfgs:
                hourly_url = self._get_hourly_forecast_url(cfg)
                payload = self._http.get_json(hourly_url)
                periods = payload.get("properties", {}).get("periods", [])
                if not isinstance(periods, list):
                    continue
                path = []
                for period in periods:
                    if not isinstance(period, dict):
                        continue
                    try:
                        start = _parse_dt(period["startTime"])
                        end = _parse_dt(period["endTime"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if end < now_utc:
                        continue
                    if start > target_time_utc + timedelta(hours=1):
                        continue
                    temp = period.get("temperature")
                    if not isinstance(temp, (int, float)):
                        continue
                    path.append((start, end, _to_f(temp, period.get("temperatureUnit")), period))
                if not path:
                    continue

                max_temp = max(t for _, _, t, _ in path)
                precip_vals = []
                for _, _, _, period in path:
                    pop = period.get("probabilityOfPrecipitation", {})
                    if isinstance(pop, dict) and isinstance(pop.get("value"), (int, float)):
                        precip_vals.append(float(pop["value"]))
                avg_pop = (sum(precip_vals) / len(precip_vals)) if precip_vals else 20.0
                cloud_vals = []
                risk_vals = []
                for _, _, _, period in path:
                    cc = _period_cloud_cover_pct(period)
                    if cc is not None:
                        cloud_vals.append(cc)
                    wr = _period_weather_risk_score(period)
                    if wr is not None:
                        risk_vals.append(wr)
                avg_cloud = (sum(cloud_vals) / len(cloud_vals)) if cloud_vals else None
                path_risk = max(risk_vals) if risk_vals else None

                hours_remaining = max(0.0, min(24.0, (target_time_utc - now_utc).total_seconds() / 3600))
                band = 2.5 + (hours_remaining / 12.0) + (avg_pop / 100.0) * 1.5
                points.append(
                    ForecastPoint(
                        source=self.name,
                        station_id=cfg.station_id,
                        city=city,
                        target_time_utc=target_time_utc,
                        expected_temp_f=max_temp,
                        low_f=max_temp - band,
                        high_f=max_temp + band,
                        confidence=max(0.3, min(0.9, 0.85 - (avg_pop / 200.0))),
                        updated_at_utc=_payload_updated_time(payload, created),
                        pop_pct=avg_pop,
                        cloud_cover_pct=avg_cloud,
                        weather_risk_score=path_risk,
                    )
                )
        return ForecastSnapshot(points=points, created_at_utc=created)

    def _get_hourly_forecast_url(self, cfg: CityWeatherConfig) -> str:
        key = (cfg.city, cfg.station_id)
        if key in self._hourly_url_cache:
            return self._hourly_url_cache[key]
        payload = self._get_points_payload(cfg)
        forecast_url = payload.get("properties", {}).get("forecastHourly")
        if not isinstance(forecast_url, str):
            raise ValueError(f"No forecastHourly URL for city={cfg.city}")
        self._hourly_url_cache[key] = forecast_url
        return forecast_url

    def _get_points_payload(self, cfg: CityWeatherConfig) -> dict[str, Any]:
        key = (cfg.city, cfg.station_id)
        if key in self._points_payload_cache:
            return self._points_payload_cache[key]
        points_url = f"https://api.weather.gov/points/{cfg.latitude},{cfg.longitude}"
        payload = self._http.get_json(points_url)
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected points payload for city={cfg.city}")
        self._points_payload_cache[key] = payload
        return payload


class AviationWeatherMetarObservationSource(ObservationSource):
    def __init__(self, *, http_client: JsonHttpClient | None = None, source_name: str = "aviationweather-metar") -> None:
        self.name = source_name
        self._http = http_client or JsonHttpClient()

    def fetch_latest(self, city: str, station_id: str) -> ObservationSnapshot:
        url = f"https://aviationweather.gov/api/data/metar?ids={station_id}&format=json"
        payload = self._http.get_json(url)
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"No METAR observations returned for {station_id}")
        row = payload[0]
        observed_at = self._extract_observed_time(row)
        temp_f = self._extract_temp_f(row)
        dewpoint_f = self._extract_optional_temp_f(row, ("dewp", "dewpoint"))
        wind_mph = self._extract_wind_mph(row)
        condition = row.get("wxString") or row.get("rawOb")
        return ObservationSnapshot(
            station_id=station_id,
            city=city,
            observed_at_utc=observed_at,
            temp_f=temp_f,
            dewpoint_f=dewpoint_f,
            wind_mph=wind_mph,
            condition=str(condition) if condition is not None else None,
            source=self.name,
        )

    @staticmethod
    def _extract_observed_time(row: dict[str, Any]) -> datetime:
        for key in ("obsTime", "observationTime", "reportTime"):
            val = row.get(key)
            if isinstance(val, str):
                try:
                    return _parse_dt(val)
                except ValueError:
                    continue
        return datetime.now(UTC)

    @staticmethod
    def _extract_temp_f(row: dict[str, Any]) -> float:
        for key in ("temp", "tempC"):
            val = row.get(key)
            if isinstance(val, (int, float)):
                if key.endswith("C"):
                    return _c_to_f(float(val))
                return _c_to_f(float(val)) if float(val) < 60 else float(val)
        raise ValueError("METAR payload missing temperature field")

    @staticmethod
    def _extract_optional_temp_f(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
        for key in keys:
            val = row.get(key)
            if isinstance(val, (int, float)):
                return _c_to_f(float(val)) if float(val) < 60 else float(val)
        return None

    @staticmethod
    def _extract_wind_mph(row: dict[str, Any]) -> float | None:
        for key in ("wspd", "windSpeed"):
            val = row.get(key)
            if isinstance(val, (int, float)):
                # AviationWeather commonly returns knots.
                return float(val) * 1.15078
        return None
