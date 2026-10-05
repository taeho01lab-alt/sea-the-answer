export class AppError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}
export function requireValue(condition, message, code = 'INVALID_INPUT', status = 400) {
  if (!condition) throw new AppError(status, code, message);
}
export function object(value) {
  requireValue(value && typeof value === 'object' && !Array.isArray(value), 'JSON 객체가 필요합니다.');
  return value;
}
export function string(value, label, max = 2000) {
  requireValue(typeof value === 'string' && value.trim().length > 0 && value.length <= max, `${label}: 1~${max}자의 문자열이 필요합니다.`);
  return value.trim();
}
export function number(value, label, min, max) {
  requireValue(typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max, `${label}: ${min}~${max} 범위의 숫자가 필요합니다.`);
  return value;
}
