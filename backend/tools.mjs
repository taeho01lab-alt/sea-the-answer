import { object, number, string, requireValue, AppError } from './validation.mjs';
import { logTool } from './db.mjs';

export const toolDefinitions = [
  { name: 'calculate_emissions', description: '사용자가 지정한 연료·계수·DWT·거리로 배출량과 단순 집약도를 계산합니다. 공식 CII 등급은 산정하지 않습니다.',
    input: { fuel: 't, 0..1e7', factor: 'tCO2/t fuel, >0..10', dwt: 't, >0..1e7', distance: 'nm, >0..1e7' } },
  { name: 'voyage_time', description: 'UTC 구간의 실제 경과시간과 수동 선내 오프셋에 따른 시계 표시 차이를 계산합니다.',
    input: { start: 'ISO 8601 UTC (...Z)', end: 'ISO 8601 UTC (...Z)', before: 'UTC offset minutes', after: 'UTC offset minutes' } },
];
export function calculateEmissions(raw) {
  object(raw);
  const fuel = number(raw.fuel, '연료(t)', 0, 1e7), factor = number(raw.factor, '환산계수', Number.MIN_VALUE, 10);
  const dwt = number(raw.dwt, '재화중량(DWT)', Number.MIN_VALUE, 1e7), distance = number(raw.distance, '거리(nm)', Number.MIN_VALUE, 1e7);
  const emission = fuel * factor, intensity = emission * 1e6 / (dwt * distance);
  requireValue(Number.isFinite(intensity), '입력 단위와 크기를 확인해 주세요.');
  return { version: 'HAEDAP-EMISSIONS-1', emission, intensity, units: { emission: 'tCO2', intensity: 'gCO2/(DWT·nm)' },
    formula: 'fuel * factor; emission * 1e6 / (dwt * distance)', officialCiiRating: null,
    assumptions: ['환산계수는 사용자 입력값입니다.', '선종별 기준선·보정계수·제외 항차를 반영하지 않습니다.'] };
}
export function voyageTime(raw) {
  object(raw);
  const parse = (value, label) => {
    const s = string(value, label, 30);
    requireValue(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$/.test(s) && Number.isFinite(Date.parse(s)), `${label}: UTC ISO 시각이 필요합니다.`);
    const iso = new Date(s).toISOString();
    requireValue(iso === (s.includes('.') ? s : s.replace('Z', '.000Z')), '존재하지 않는 날짜입니다.');
    return Date.parse(s);
  };
  const start = parse(raw.start, '시작 시각'), end = parse(raw.end, '종료 시각');
  requireValue(end >= start && end - start <= 366 * 86400000, '종료는 시작 이후 366일 이내여야 합니다.');
  const before = number(raw.before, '시작 오프셋', -720, 840), after = number(raw.after, '종료 오프셋', -720, 840);
  requireValue(Number.isInteger(before) && Number.isInteger(after) && before % 15 === 0 && after % 15 === 0, '오프셋은 15분 단위입니다.');
  return { version: 'HAEDAP-TIME-1', elapsedHours: (end - start) / 3600000,
    clockHours: (end - start) / 3600000 + (after - before) / 60,
    shipStart: new Date(start + before * 60000).toISOString().slice(0, -1), shipEnd: new Date(end + after * 60000).toISOString().slice(0, -1),
    interval: '[start,end)', assumptions: ['선내 UTC 오프셋은 사용자 입력입니다. 자동 시간대·날짜변경선 판정은 수행하지 않습니다.'] };
}
export function runTool(db, name, input) {
  const fn = { calculate_emissions: calculateEmissions, voyage_time: voyageTime };
  if (!Object.hasOwn(fn, name)) throw new AppError(404, 'TOOL_NOT_FOUND', '등록되지 않은 도구입니다.');
  return logTool(db, name, input, fn[name](input));
}
