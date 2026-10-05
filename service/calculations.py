"""Deterministic Python tools; the language model never supplies calculated numbers."""
from decimal import Decimal
from datetime import date
from pydantic import BaseModel, ConfigDict, Field, field_validator

class NoonInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    vessel_id: str = Field(min_length=1, max_length=80)
    date: date
    voyage: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=80)
    latitude: float = Field(ge=-90, le=90, strict=True)
    longitude: float = Field(ge=-180, le=180, strict=True)
    speed_kn: float = Field(ge=0, le=60, strict=True)
    distance_nm: float = Field(gt=0, le=2000, strict=True)
    fuel_t: float = Field(ge=0, le=2000, strict=True)
    factor: float = Field(gt=0, le=10, strict=True)
    engine_hours: float = Field(ge=0, le=24, strict=True)
    draft_m: float = Field(ge=0, le=40, strict=True)
    weather: str = Field(min_length=1, max_length=200)

def metrics(vessel, records):
    if not records: raise ValueError("선택한 선박·기간에 운항 데이터가 없습니다.")
    dwt = Decimal(str(vessel.dwt))
    distance = sum(Decimal(str(r.data["distance_nm"])) for r in records)
    fuel = sum(Decimal(str(r.data["fuel_t"])) for r in records)
    emission = sum(Decimal(str(r.data["fuel_t"])) * Decimal(str(r.data["factor"])) for r in records)
    if dwt <= 0 or distance <= 0: raise ValueError("DWT와 항해 거리는 0보다 커야 합니다.")
    series = [{"date": r.date, "fuel_t": r.data["fuel_t"], "distance_nm": r.data["distance_nm"],
               "emission_t": float(Decimal(str(r.data["fuel_t"])) * Decimal(str(r.data["factor"])))} for r in records]
    anomalies = []
    for before, after in zip(records, records[1:]):
        if (date.fromisoformat(after.date) - date.fromisoformat(before.date)).days == 1 and before.data["fuel_t"] > 0:
            ratio = after.data["fuel_t"] / before.data["fuel_t"]
            if ratio > 2 or ratio < .5: anomalies.append({"date": after.date, "field": "fuel_t", "message": "전일 대비 연료 사용량이 2배 초과 또는 절반 미만입니다. 원자료를 확인하세요."})
    return {"tool": "voyage_metrics", "version": "PY-METRICS-1", "fuel_t": float(fuel), "distance_nm": float(distance),
            "emission_t": float(emission), "intensity": float(emission * 1_000_000 / (dwt * distance)),
            "average_speed_kn": sum(r.data["speed_kn"] for r in records) / len(records),
            "units": {"fuel_t": "t", "emission_t": "tCO2", "intensity": "gCO2/(DWT·nm)"},
            "formula": "Σ(fuel_t × factor); CO2_t × 10^6 / (DWT × Σdistance_nm)",
            "official_cii_rating": None, "compliance": "not_assessed",
            "notice": "기간 단순 탄소집약도입니다. 연간 완전성·선종별 기준선·보정 및 제외 항차를 검증하지 않아 공식 CII 등급과 규정 적합성은 판정하지 않습니다.",
            "inputs": {"vessel_id": vessel.id, "dwt": vessel.dwt, "records": [{"id": r.id, "date": r.date, "version": r.version, **r.data} for r in records]},
            "series": series, "anomalies": anomalies}

def report_text(kind, vessel, result, evidence, language="ko"):
    label = "FICTIONAL SAMPLE" if vessel.sample else "USER-SUPPLIED DATA"
    lines = [f"# {kind} — {vessel.name}", f"DRAFT / 담당자 검토 필요 · {label}",
             f"Period: {result['series'][0]['date']} ~ {result['series'][-1]['date']}",
             f"Vessel: {vessel.id} / {vessel.ship_type} / DWT {vessel.dwt} t"]
    if kind == "Noon Report":
        for r in result["inputs"]["records"]:
            lines += [f"\n## {r['date']} · Voyage {r['voyage']}", f"Status: {r['status']} / Position: {r['latitude']}, {r['longitude']}",
                      f"Speed: {r['speed_kn']} kn / Distance: {r['distance_nm']} nm / Fuel: {r['fuel_t']} t",
                      f"Engine: {r['engine_hours']} h / Draft: {r['draft_m']} m / Weather: {r['weather']}"]
    lines += [f"\nCO2: {result['emission_t']:.6f} tCO2", f"Fuel: {result['fuel_t']:.6f} t / Distance: {result['distance_nm']:.3f} nm",
              f"Intensity: {result['intensity']:.6f} gCO2/(DWT·nm)", f"Formula: {result['formula']}", result["notice"],
              "MRV 제출 적합성이 검증된 공식 양식이 아닌 업무 검토용 초안입니다." if kind == "MRV Report" else "선장·담당자 확인 후 별도 업무 절차로 확정하세요.", "\n## Evidence / 근거"]
    lines += [f"- {e['title']} · v{e['version']} · page {e['page'] or 'N/A'} · {e['section']} [{e['id']}]\n  {e['text']}" for e in evidence]
    if not evidence: lines.append("등록된 문서 근거 없음 / No document evidence")
    return "\n".join(lines)
