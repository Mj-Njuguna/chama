/**
 * A UUID v4 for `installation_id`. It is a device label, not a secret (the per-device limit uses
 * it; sessions are bound to tokens), so Math.random is acceptable until the native module can
 * provide one from SecureRandom. No crypto dependency is added for it.
 */
export function newInstallationId(random: () => number = Math.random): string {
  const hex = Array.from({ length: 32 }, () => Math.floor(random() * 16).toString(16));
  hex[12] = '4';
  hex[16] = ((parseInt(hex[16] ?? '0', 16) & 0x3) | 0x8).toString(16);
  const s = hex.join('');
  return `${s.slice(0, 8)}-${s.slice(8, 12)}-${s.slice(12, 16)}-${s.slice(16, 20)}-${s.slice(20)}`;
}
